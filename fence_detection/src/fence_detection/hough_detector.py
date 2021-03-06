import roslib
roslib.load_manifest('fence_detection')

import rospy
import numpy as np
import cv2.cv as cv

from scipy.cluster.vq import kmeans2

import planar.line

# ROS Message Imports
from geometry_msgs.msg import Point
from visualization_msgs.msg import Marker


class Scan(object):
    def __init__(self):
        self.angle_min = 0
        self.angle_max = 0
        self.angle_increment = 0
        self.range_max = 0
        self.range_min = 0
        self.ranges = 0

    def __len__(self):
        return self.ranges.shape[1]

    @property
    def rectangular(self):
        """ The rectangular property."""
        return self.ranges[3:5, :]

    @property
    def polar(self):
        """ The polar property."""
        return self.ranges[0:2, :]

    @property
    def theta(self):
        return self.ranges[0, :]

    @property
    def rho(self):
        """ The rho property."""
        return self.ranges[1, :]

    def get_partition_by_index(self, start, end):
        """
        Get a new Scan object of [start:end] (inclusive) indicies
        """
        if start < 0:
            raise ValueError("Start index must be >= 0")
        if end > self.ranges.shape[1]:
            raise ValueError("End index must be <= len(scan)")
        if start >= end:
            raise ValueError("Start must be < end")
        ret = Scan()
        ret.angle_min = self.ranges[0, start]
        ret.angle_max = self.ranges[0, end]
        ret.angle_increment = self.angle_increment
        ret.timestamp = self.timestamp
        ret.ranges = self.ranges[:, start:end + 1].copy()
        ret.range_min = np.amin(ret.ranges, 1)[1]
        ret.range_max = np.amax(ret.ranges, 1)[1]
        return ret

    def get_partition_by_angle(self, start, end):
        """
        Get a new Scan object of [start:end] angles
        """
        if start < self.angle_min:
            raise ValueError("Start angle must be >= angle_min")
        if end > self.angle_max:
            raise ValueError("End angle must be <= angle_max")
        if start >= end:
            raise ValueError("Start must be < end")
        index_min = np.searchsorted(self.ranges[0, :], start, side='right') - 1
        index_max = np.searchsorted(self.ranges[0, :], end)
        return self.get_partition_by_index(index_min, index_max)

    @classmethod
    def from_LaserScan(cls, scan_msg):
        ret = Scan()

        ret.angle_min = scan_msg.angle_min
        ret.angle_max = scan_msg.angle_max
        ret.angle_increment = scan_msg.angle_increment
        ret.range_min = scan_msg.range_min
        ret.range_max = scan_msg.range_max
        ret.timestamp = scan_msg.header.stamp
        ret.frame_id = scan_msg.header.frame_id

        temp = np.zeros((5, len(scan_msg.ranges)), dtype=np.float32)
        theta = np.arange(ret.angle_min, ret.angle_max, ret.angle_increment)
        try:
            temp[0, :] = theta
        except:
            temp[0, :] = np.arange(ret.angle_min, ret.angle_max + ret.angle_increment, ret.angle_increment)
        temp[1, :] = scan_msg.ranges
        for ii in range(len(temp[1, :])):
            if temp[1, ii] <= 1.0:
                temp[2, ii] = 0.03 ** 2
            else:
                temp[2, ii] = (0.03 * temp[1, ii]) ** 2
        temp[3, :] = temp[1, :] * np.cos(temp[0, :])
        temp[4, :] = temp[1, :] * np.sin(temp[0, :])
        ret.ranges = temp
        return ret


class HoughDetector(object):
    def __init__(self, resolution=0.01, pixel_res=5, theta_res=np.pi / 180,
                 accum_thresh=40, min_length=100, gap_length=100):
        self.scan = None
        self.timestamp = None
        self.resolution = resolution
        self.pixel_res = pixel_res
        self.theta_res = theta_res
        self.accum_thresh = accum_thresh
        self.min_length = min_length
        self.gap_length = gap_length

    def add_data_from_msg(self, msg, timestamp=None):
        self.scan = Scan.from_LaserScan(msg)
        self.timestamp = timestamp
        self.xy = self.scan.ranges[3:, :]

        (self.minx, self.miny) = np.min(self.xy, 1)
        (self.maxx, self.maxy) = np.max(self.xy, 1)

    def process(self):
        range_x = np.ceil((self.maxx - self.minx) * (1. / self.resolution)) + 1
        range_y = np.ceil((self.maxy - self.miny) * (1. / self.resolution)) + 1

        np_src = np.zeros((range_x, range_y), dtype=np.uint8)
        for el in self.xy.T:
            el_x = np.ceil((el[0] - self.minx) * (1. / self.resolution))
            el_y = np.ceil((el[1] - self.miny) * (1. / self.resolution))
            np_src[el_x, el_y] = 255

        src = cv.fromarray(np_src)
        storage = cv.CreateMemStorage(0)
        self.lines = 0
        self.lines = cv.HoughLines2(src, storage, cv.CV_HOUGH_PROBABILISTIC,
                               self.pixel_res, self.theta_res,
                               threshold=self.accum_thresh,
                               param1=self.min_length,
                               param2=self.gap_length)

    def get_lines_pixels(self):
        return self.lines

    def get_lines_meters(self):
        lines = []
        for (pt1, pt2) in self.lines:
            x1 = (pt1[0] * self.resolution) + self.miny
            y1 = (pt1[1] * self.resolution) + self.minx
            x2 = (pt2[0] * self.resolution) + self.miny
            y2 = (pt2[1] * self.resolution) + self.minx
            lines.append(((x1, y1), (x2, y2)))
        return lines

    def get_fence_position(self):
        lineobjs = [planar.line.LineSegment.from_points(l) for l in self.get_lines_meters()]
        normals = np.zeros(len(lineobjs, 2))

        for i, l in enumerate(lineobjs):
            normals[i, :] = l.normal

        (means, index) = kmeans2(normals, 2)
        front = []
        side = []
        for i in index:
            if i == l:
                front.append(lineobjs[i])
            else:
                side.append(lineobjs[i])
        front_vec = front.pop()
        side_vec = side.pop()

        for l in front:
            if l.distance_to((0, 0)) < front_vec.distance_to((0, 0)):
                front_vec = l
        for l in side:
            if l.distance_to((0, 0)) < side_vec.distance_to((0, 0)):
                side_vec = l

        (x, y) = self.intersection(front_vec, side_vec)
        return (x, y, 0)

    def intersection(self, line1, line2):
        (x1, y1), (x2, y2) = line1.points
        (x3, y3), (x4, y4) = line2.points

        den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        P_x = ((x1 * y2 - y1 * x2) * (x3 - x4) -
            (x1 - x2) * (x3 * y4 - y3 * x4)) / den
        P_y = ((x1 * y2 - y1 * x2) * (y3 - y4) -
            (y1 - y2) * (x3 * y4 - y3 * x4)) / den
        return (P_x, P_y)

    def get_visualization_markers(self, type='hough_lines'):
        """
        Returns a visualization_msgs.Marker Message
        with the lines from the Hough Transform in the frame of the scanner.
        """
        if type == 'hough_lines':
            marker = Marker()
            marker.header.frame_id = self.scan.frame_id
            marker.header.stamp = self.scan.timestamp
            marker.ns = "hough_lines"
            marker.id = 0
            marker.scale.x = 0.01
            marker.scale.y = 0.01
            marker.scale.z = 0.01
            marker.type = marker.LINE_LIST
            marker.action = marker.ADD
            marker.lifetime = rospy.Duration()
            marker.points = []
            for (pt1, pt2) in self.get_lines_meters():
                marker.points.append(Point(pt1[1], pt1[0], 0))
                marker.points.append(Point(pt2[1], pt2[0], 0))
            marker.color.r = 0.0
            marker.color.g = 1.0
            marker.color.b = 0.0
            marker.color.a = 1.0
        return marker
