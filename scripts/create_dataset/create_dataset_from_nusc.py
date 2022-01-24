#!/usr/bin/env python3
# coding: utf-8

import argparse
import copy
import math
import os
import sys

import numba
import numpy as np
from pyquaternion import Quaternion

# import feature_generator as fg
import feature_generator_pb as fgpb


try:
    from nuscenes.nuscenes import NuScenes
    from nuscenes.utils.data_classes import LidarPointCloud
except ImportError:
    for path in sys.path:
        if '/opt/ros/' in path:
            print('sys.path.remove({})'.format(path))
            sys.path.remove(path)
            from nuscenes.nuscenes import NuScenes
            from nuscenes.utils.data_classes import LidarPointCloud
            sys.path.append(path)
            break


def add_noise_points(points, num_rand_samples=5,
                     min_distance=5, sigma=2, add_noise_rate=0.1):
    """Add noise to the point cloud

    Parameters
    ----------
    points : numpy.ndarray
        Input point cloud. (n, 4)
    num_rand_samples : int, optional
        How many sample points to take at one angle, by default 5
    min_distance : int, optional
        Closest distance of noise, by default 5
    sigma : int, optional
        normal distribution of z, by default 2
    add_noise_rate : float, optional
        Percentage of angles to add noise, by default 0.1

    Returns
    -------
    points : numpy.ndarray
        Point cloud with added noise. (n, 4)

    """
    max_height = np.max(points[:, 2])
    min_height = np.min(points[:, 2])
    mean_height = np.mean(points[:, 2])

    distances = np.linalg.norm(
        np.hstack([points[:, 0:1], points[:, 1:2]]), axis=1)
    max_distance = np.max(distances)

    noise_points = []
    for theta in range(360):
        if np.random.rand() > add_noise_rate:
            continue
        distance = np.min(np.random.uniform(
            min_distance, max_distance, num_rand_samples))
        z = np.random.normal(mean_height, sigma)
        while min_height > z or z > max_height:
            z = np.random.normal(mean_height, sigma)
        x = distance * np.cos(theta)
        y = distance * np.sin(theta)
        i = points[np.random.randint(0, points.shape[0]), 3]
        noise_points.append([x, y, z, i])

    noise_points = np.array(noise_points)
    points = np.vstack([points, noise_points])

    return points


def create_dataset(dataroot, save_dir, width=672, height=672, grid_range=70.,
                   nusc_version='v1.0-mini',
                   use_constant_feature=False, use_intensity_feature=True,
                   end_id=None, augmentation_num=0, add_noise=False):
    """Create a learning dataset from Nuscens

    Parameters
    ----------
    dataroot : str
        Nuscenes dataroot path.
    save_dir : str
        Dataset save directory.
    width : int, optional
        feature map width, by default 672
    height : int, optional
        feature map height, by default 672
    grid_range : float, optional
        feature map range, by default 70.
    nusc_version : str, optional
        Nuscenes version. v1.0-mini or v1.0-trainval, by default 'v1.0-mini'
    use_constant_feature : bool, optional
        Whether to use constant feature, by default False
    use_intensity_feature : bool, optional
        Whether to use intensity feature, by default True
    end_id : int, optional
        How many data to generate. If None, all data, by default None
    augmentation_num : int, optional
        How many data augmentations for one sample, by default 0
    add_noise : bool, optional
        Whether to add noise to pointcloud, by default True

    Raises
    ------
    Exception
        Width and height are not equal

    """
    os.makedirs(os.path.join(save_dir, 'in_feature'), exist_ok=True)
    os.makedirs(os.path.join(save_dir, 'out_feature'), exist_ok=True)

    nusc = NuScenes(
        version=nusc_version,
        dataroot=dataroot, verbose=True)
    ref_chan = 'LIDAR_TOP'

    if width == height:
        size = width
    else:
        raise Exception(
            'Currently only supported if width and height are equal')

    grid_length = 2. * grid_range / size
    z_trans_range = 0.5
    sample_id = 0
    data_id = 0
    grid_ticks = np.arange(
        -grid_range, grid_range + grid_length, grid_length)
    grid_centers \
        = (grid_ticks + grid_length / 2)[:len(grid_ticks) - 1]

    for my_scene in nusc.scene:
        first_sample_token = my_scene['first_sample_token']
        token = first_sample_token

        # try:
        while(token != ''):
            print('sample:{} {} created_data={}'.format(
                sample_id, token, data_id))
            my_sample = nusc.get('sample', token)
            sd_record = nusc.get(
                'sample_data', my_sample['data'][ref_chan])
            sample_rec = nusc.get('sample', sd_record['sample_token'])
            chan = sd_record['channel']
            pc_raw, _ = LidarPointCloud.from_file_multisweep(
                nusc, sample_rec, chan, ref_chan, nsweeps=1)

            _, boxes_raw, _ = nusc.get_sample_data(
                sd_record['token'], box_vis_level=0)

            z_trans = 0
            q = Quaternion()
            for augmentation_idx in range(augmentation_num + 1):
                pc = copy.copy(pc_raw)
                if add_noise:
                    pc.points = add_noise_points(pc.points.T).T
                boxes = copy.copy(boxes_raw)
                if augmentation_idx > 0:
                    z_trans = (np.random.rand() - 0.5) * 2 * z_trans_range
                    pc.translate([0, 0, z_trans])

                    z_rad = np.random.rand() * np.pi * 2
                    q = Quaternion(axis=[0, 0, 1], radians=z_rad)
                    pc.rotate(q.rotation_matrix)

                pc_points = pc.points.astype(np.float32)

                out_feature = np.zeros((size, size, 8), dtype=np.float32)
                for box_idx, box in enumerate(boxes):
                    if augmentation_idx > 0:
                        box.translate([0, 0, z_trans])
                        box.rotate(q)

                    label = 0
                    if box.name.split('.')[0] == 'vehicle':
                        if box.name.split('.')[1] == 'car':
                            label = 1
                        elif box.name.split('.')[1] == 'bus':
                            label = 2
                        elif box.name.split('.')[1] == 'truck':
                            label = 2
                        elif box.name.split('.')[1] == 'construction':
                            label = 2
                        elif box.name.split('.')[1] == 'emergency':
                            label = 2
                        elif box.name.split('.')[1] == 'trailer':
                            label = 2
                        elif box.name.split('.')[1] == 'bicycle':
                            label = 3
                        elif box.name.split('.')[1] == 'motorcycle':
                            label = 3
                    elif box.name.split('.')[0] == 'human':
                        label = 4
                    # elif box.name.split('.')[0] == 'movable_object':
                    #     label = 1
                    # elif box.name.split('.')[0] == 'static_object':
                    #     label = 1
                    else:
                        continue
                    height_pt = np.linalg.norm(
                        box.corners().T[0] - box.corners().T[3])
                    box_corners = box.corners().astype(np.float32)
                    corners2d = box_corners[:2, :]
                    box2d = corners2d.T[[2, 3, 7, 6]]
                    box2d_center = box2d.mean(axis=0)
                    yaw, pitch, roll = box.orientation.yaw_pitch_roll
                    out_feature = generate_out_feature(
                        size, grid_centers, box_corners,
                        box2d, box2d_center, pc_points,
                        height_pt, label, yaw, out_feature)

                # feature_generator = fg.FeatureGenerator(
                #     grid_range, width, height,
                #     use_constant_feature, use_intensity_feature)
                feature_generator = fgpb.FeatureGenerator(
                    grid_range, size, size)
                in_feature = feature_generator.generate(
                    pc_points.T,
                    use_constant_feature, use_intensity_feature)

                if use_constant_feature and use_intensity_feature:
                    channels = 8
                elif use_constant_feature or use_intensity_feature:
                    channels = 6
                else:
                    channels = 4

                in_feature = np.array(in_feature).reshape(
                    channels, size, size).astype(np.float16)
                in_feature = in_feature.transpose(1, 2, 0)
                np.save(os.path.join(
                    save_dir, 'in_feature/{:05}'.format(data_id)),
                    in_feature)
                np.save(os.path.join(
                    save_dir, 'out_feature/{:05}'.format(data_id)),
                    out_feature)
                token = my_sample['next']
                data_id += 1
                if data_id == end_id:
                    return
            sample_id += 1
        # except KeyboardInterrupt:
        #     return
        # except BaseException:
        #     print('skipped')
        #     continue


@numba.jit(nopython=True)
def generate_out_feature(size, grid_centers, box_corners,
                         box2d, box2d_center, pc_points,
                         height_pt, label, yaw, out_feature):
    """Generate out_feature.

    Parameters
    ----------
    size : int
        feature map size
    grid_centers : numpy.ndarray
        center coordinates of feature_map grid
    box_corners : numpy.ndarray
        The coordinates of each corner of the object's box.
    box2d : numpy.ndarray
        The x,y coordinates of the object's box
    box2d_center : numpy.ndarray
        Center x,y coordinates of object's box
    pc_points : numpy.ndarray
        Input point cloud. (4, n)
    height_pt : float
        Height of object.
    label : int
        Object label. classify_pt
    yaw : float
        Rotation of the yaw of the object. heading_pt.
    out_feature : numpy.ndarray
        Output features. category, instance(x, y),
        confidence, classify, heading(x, y), height

    Returns
    -------
    out_feature : numpy.ndarray
        Output features. category, instance(x, y),
        confidence, classify, heading(x, y), height

    """
    box2d_left = box2d[:, 0].min()
    box2d_right = box2d[:, 0].max()
    box2d_top = box2d[:, 1].max()
    box2d_bottom = box2d[:, 1].min()

    def points_in_box(corners, points, wlh_factor=1.0):
        """Check whether points are inside the box.

        Partially changed the function implemented in
        "https://github.com/nutonomy/nuscenes-devkit/blob/master/python-sdk/nuscenes/utils/geometry_utils.py"

        Picks one corner as reference (p1) and computes
        the vector to a target point (v).
        Then for each of the 3 axes, project v onto the axis
        and compare the length.
        Inspired by: https://math.stackexchange.com/a/1552579

        :param box: <Box>.
        :param points: <np.float: 3, n>.
        :param wlh_factor: Inflates or deflates the box.
        :return: <np.bool: n, >.

        """
        p1 = corners[:, 0]
        p_x = corners[:, 4]
        p_y = corners[:, 1]
        p_z = corners[:, 3]

        pi = p_x - p1
        pj = p_y - p1
        pk = p_z - p1

        v = points - np.array([[p1[0]], [p1[1]], [p1[2]]]).astype(np.float32)

        iv = np.dot(pi, v)
        jv = np.dot(pj, v)
        kv = np.dot(pk, v)

        mask_x = np.logical_and(0 <= iv, iv <= np.dot(pi, pi))
        mask_y = np.logical_and(0 <= jv, jv <= np.dot(pj, pj))
        mask_z = np.logical_and(0 <= kv, kv <= np.dot(pk, pk))
        mask = np.logical_and(np.logical_and(mask_x, mask_y), mask_z)

        return mask

    def points_in_box2d(corners, box2d, points):
        """2D version of points_in_box"""
        p1 = box2d[0]
        p_x = box2d[1]
        p_y = box2d[3]

        pi = p_x - p1
        pj = p_y - p1

        v = points[:2] - p1

        iv = np.dot(pi, v)
        jv = np.dot(pj, v)

        mask_x = np.logical_and(0 <= iv, iv <= np.dot(pi, pi))
        mask_y = np.logical_and(0 <= jv, jv <= np.dot(pj, pj))
        mask = np.logical_and(mask_x, mask_y)

        return mask

    def F2I(val, orig, scale):
        """Convert points in lidar coordinate system to feature_map coordinate system."""
        return int(np.floor((orig - val) * scale))

    def Pixel2pc(in_pixel, in_size, out_range):
        """Convert points in feature_map coordinate system to lidar coordinate system."""

        res = 2.0 * out_range / in_size
        return out_range - (in_pixel + 0.5) * res

    inv_res = 0.5 * size / 70.
    res = 1.0 / inv_res
    max_length = abs(2 * res)

    search_area_left_idx = F2I(box2d_left, 70, inv_res)
    search_area_right_idx = F2I(box2d_right, 70, inv_res)
    search_area_top_idx = F2I(box2d_top, 70, inv_res)
    search_area_bottom_idx = F2I(box2d_bottom, 70, inv_res)

    num_points = np.count_nonzero(points_in_box(box_corners, pc_points[:3, :]))
    if num_points < 4 and label == 0:
        return out_feature
    elif num_points < 4 and label == 1:
        return out_feature
    elif num_points < 4 and label == 2:
        return out_feature
    elif num_points < 4 and label == 3:
        return out_feature
    elif num_points < 4 and label == 4:
        return out_feature

    for i in range(
            search_area_right_idx - 1, search_area_left_idx + 1):
        for j in range(
                search_area_top_idx - 1, search_area_bottom_idx + 1):
            if 0 <= i and i < size and 0 <= j and j < size:
                grid_center_x = Pixel2pc(i, float(size), 70)
                grid_center_y = Pixel2pc(j, float(size), 70)

                if max_length < np.abs(box2d_center[0] - grid_center_x):
                    x_scale = max_length / \
                        np.abs(box2d_center[0] - grid_center_x)
                else:
                    x_scale = 1.
                if max_length < np.abs(box2d_center[1] - grid_center_y):
                    y_scale = max_length / \
                        np.abs(box2d_center[1] - grid_center_y)
                else:
                    y_scale = 1.

                normalized_yaw = math.atan(math.sin(yaw) / math.cos(yaw))

                mask = points_in_box2d(
                    box_corners, box2d,
                    np.array([grid_center_x, grid_center_y, 0]).astype(np.float32))

                if mask:
                    out_feature[i, j, 0] = 1.  # category_pt
                    out_feature[i, j, 1] = (
                        (box2d_center[0] - grid_center_x) * -1) * min(x_scale, y_scale)
                    out_feature[i, j, 2] = (
                        (box2d_center[1] - grid_center_y) * -1) * min(x_scale, y_scale)
                    out_feature[i, j, 3] = 1.  # confidence_pt
                    out_feature[i, j, 4] = label  # classify_pt
                    out_feature[i, j, 5] = math.cos(normalized_yaw * 2.0)
                    out_feature[i, j, 6] = math.sin(normalized_yaw * 2.0)
                    out_feature[i, j, 7] = height_pt  # height_pt

    return out_feature


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument('--dataroot', '-dr', type=str,
                        help='Nuscenes dataroot path',
                        default='/media/holo/My Passport1/per_datasets/nuScenes/nuScenes_v1.0-mini')
    parser.add_argument('--save_dir', '-sd', type=str,
                        help='Dataset save directory',
                        default='/media/holo/My Passport1/per_datasets/nuScenes/nuScenes_baiducnn')
    parser.add_argument('--width', type=int,
                        help='feature map width',
                        default=672)
    parser.add_argument('--height', type=int,
                        help='feature map height',
                        default=672)
    parser.add_argument('--range', type=int,
                        help='feature map range',
                        default=70)
    parser.add_argument('--nusc_version', type=str,
                        help='Nuscenes version. v1.0-mini or v1.0-trainval',
                        default='v1.0-mini')
    parser.add_argument('--use_constant_feature', type=int,
                        help='Whether to use constant feature',
                        default=0)
    parser.add_argument('--use_intensity_feature', type=int,
                        help='Whether to use intensity feature',
                        default=1)
    parser.add_argument('--end_id', type=int,
                        help='How many data to generate. If None, all data',
                        default=None)
    parser.add_argument('--augmentation_num', '-an', type=int,
                        help='How many data augmentations for one sample',
                        default=0)
    parser.add_argument('--add_noise', type=int,
                        help='Whether to add noise to pointcloud',
                        default=0)

    args = parser.parse_args()
    create_dataset(dataroot=args.dataroot,
                   save_dir=args.save_dir,
                   width=args.width,
                   height=args.height,
                   grid_range=args.range,
                   nusc_version=args.nusc_version,
                   use_constant_feature=args.use_constant_feature,
                   use_intensity_feature=args.use_intensity_feature,
                   end_id=args.end_id,
                   augmentation_num=args.augmentation_num,
                   add_noise=args.add_noise)
