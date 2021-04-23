#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
We assume that arm and angular velocity control is used, otherwise it is not implemented.
"""

import gym
import rospy
from termcolor import colored
import numpy as np
from gym import spaces
from control.msg import State
from monitor.srv import StepReturn, StepReturnRequest
from monitor.srv import NewRollout, NewRolloutRequest
from monitor.srv import ObsActCompl, ObsActComplResponse, ObsActComplRequest
from std_srvs.srv import Trigger, TriggerRequest
from simulation.srv import RobotSpawn, RobotSpawnResponse, RobotSpawnRequest
from simulation.srv import EnvGen, EnvGenResponse, EnvGenRequest
from simulation.srv import OdomInfo, OdomInfoResponse, OdomInfoRequest
from perception.msg import BeamMsg
from sensor_msgs.msg import Imu
from simulation.msg import DistDirec


class TrainingEnv(gym.Env):
    ACTION_TIME = 0.25
    def __init__(self, **kwargs):
        rospy.set_param("exp_series_name", kwargs['experiment_series'])
        self.experiment = kwargs['experiment']
        self.arm_is_used = kwargs['arm']
        self.angular_is_used = kwargs['angular']
        self.sigma = kwargs['sigma']
        self.task = kwargs['task']
        self.penalty_angular = bool(kwargs['penalty_angular'])
        self.penalty_deviation = bool(kwargs['penalty_deviation'])
        self.time_step_limit = int(kwargs['time_step_limit'])
        self.obstacle = self.replace_task_obstacle(self.task)
        self.rand = "1" if kwargs['rand'] else "0"
        self.seq = 0
        # TODO it should be variable in future
        self.sensors_info = "depth_"+str(rospy.get_param("feature_height"))+"_"+str(rospy.get_param("feature_width"))
        #####
        # linear and angular are set, other fields are incremental
        self.action = State()
        # construct action fields
        self.active_action_fields = self.build_action_fields()
        # Publishers
        self.pub_robot_cmd = rospy.Publisher("robot_cmd", State)
        # Subscirbers
        rospy.Subscriber("/robot/state", State, self.update_state)
        self.robot_state = State()
        rospy.Subscriber("/features", BeamMsg, self.update_features)
        self.features = BeamMsg()
        rospy.Subscriber("/direction", DistDirec, self.update_features)
        self.direction = DistDirec()
        # Service callers
        self.step_return = rospy.ServiceProxy("/rollout/step_return", StepReturn)
        self.robot_spawn = rospy.ServiceProxy('robot_spawn', RobotSpawn)
        self.robot_reset = rospy.ServiceProxy('/robot/reset', Trigger)
        self.env_gen = rospy.ServiceProxy('env_gen', EnvGen)
        self.new_rollout = rospy.ServiceProxy('/rollout/new', NewRollout)
        self.start_rollout = rospy.ServiceProxy('/rollout/start', Trigger)
        self.odom_info = rospy.ServiceProxy('/odom_info', OdomInfo)
        # Spaces
        self.action_space, self.observation_space = self.get_spaces()
        # Currently, there are 3 levels:
        # 0 - obs.:all without horizontal features; act.: linear, front fl., rear fl.
        # 1 - obs.:all without horizontal features; act.: linear, front fl., rear fl., arm1, arm2
        # 2 - obs.:all; act.: all (linear, angular, front fl., rear fl., arm1, arm2)
        self.complexity = 2
        # Service
        s = rospy.Service('obs_act_compl', ObsActCompl, self.callback_complexity)

    def callback_complexity(self, req):
        self.complexity = req.level

    def get_spaces(self):
        ANGLE =np.pi / 4
        dMA = np.pi / 10.0
        VEL = 1.0
        amin = []
        amax = []
        omin = []
        omax = []
        fmin = []
        fmax = []

        for k, v in self.active_action_fields.items():
            if v == "angular" or v == "linear":
                if v == "angular":
                    amin.append(-VEL)
                elif v == "linear":
                    amin.append(0)
                amax.append(VEL)
                omin.append(-VEL)
                omax.append(VEL)
            elif v == "front_flippers" or v == "rear_flippers" or v == "arm_joint1" or v == "arm_joint2":
                amin.append(-dMA)
                amax.append(dMA)
                omin.append(-ANGLE)
                omax.append(ANGLE)

        height = rospy.get_param("feature_height")
        width = rospy.get_param("feature_width")
        fmin = [0.0 for i in range(height+width)]
        fmax = [3.0 for i in range(height+width)]
        omin += fmin
        omax += fmax

        # Angle2Goal
        ANGLE_MIN = -np.pi
        ANGLE_MAX = np.pi
        omin += [ANGLE_MIN]
        omax += [ANGLE_MAX]

        # Distance2Goal
        DIST_MIN = 0.
        DIST_MAX = 7.
        omin += [DIST_MIN]
        omax += [DIST_MAX]

        # Roll, pitch
        omin += [ANGLE_MIN, ANGLE_MIN]
        omax += [ANGLE_MAX, ANGLE_MAX]

        aspace = spaces.Box(np.array(amin), np.array(amax))
        ospace = spaces.Box(np.array(omin), np.array(omax))
        return aspace, ospace

    def build_action_fields(self):
        d = {0: 'linear'}
        index = 1
        if self.angular_is_used:
            d[index] = 'angular'
            index += 1
        d[index] = 'front_flippers'
        index += 1
        d[index] = 'rear_flippers'
        index += 1
        if self.arm_is_used:
            d[index] = 'arm_joint1'
            index += 1
            d[index] = 'arm_joint2'
            index += 1
        return d

    def replace_task_obstacle(self, task):
        obstacle = ""
        if self.task == "flat":
            obstacle = "ground_obstacles"
        elif self.task == "ascent" or self.task == "descent":
            obstacle = "stair_floor"
        else:
            raise(NotImplementedError())
        return obstacle

    def update_direction(self, msg):
        self.direction = msg

    def update_features(self, msg):
        self.features = msg

    def update_state(self, msg):
        self.robot_state = msg

    def update_action(self, action):
        """
        Possible configurations:
        action => [linear, angular, front_flippers, rear_flippers, arm_joint1, arm_joint2]
        action => [linear, front_flippers, rear_flippers, arm_joint1, arm_joint2]
        action => [linear, angular, front_flippers, rear_flippers]
        action => [linear, front_flippers, rear_flippers]
        :param action:
        :return:
        """
        # for i, action_value in enumerate(action):
        #     setattr(self.action, self.active_action_fields[i], action_value)
        constraint_fields = {
            0: ["angular", "arm_joint1", "arm_joint2"],
            1: ["angular"],
            2: []
        }
        for i, action_value in enumerate(action):
            if self.active_action_fields[i] in constraint_fields[self.complexity]:
                if "arm" in self.active_action_fields[i]:
                    action_value = np.pi / 4
                else:
                    action_value = 0
            setattr(self.action, self.active_action_fields[i], action_value)

    def get_transformed_state(self):
        """
        state <= [robot state, vertical, horizontal, rpy]
        :return:
        """
        state = []
        # robot configuration
        for k, v in self.active_action_fields.items():
            state.append(getattr(self.robot_state, v, 0.))

        # features
        state += self.features.vertical.data

        # set observation input to zero for low complexities
        if self.complexity == 2:
            state += self.features.horizontal.data
        else:
            state += [0.0 for i in range(len(self.features.horizontal.data))]

        # direction + distance
        state += [self.direction.angle, self.direction.distance]

        # pitch
        resp = self.odom_info.call(OdomInfoRequest())
        state += [resp.roll, resp.pitch]
        return state

    def regenerate_obstacles(self):
        print("regenerating", self.obstacle, self.task + "_" + self.rand)
        self.env_gen.call(
            EnvGenRequest(
                action="delete",
                model=self.obstacle,
                props=self.task + "_" + self.rand,
            )
        )
        self.env_gen.call(
            EnvGenRequest(
                action="generate",
                model=self.obstacle,
                props=self.task + "_" + self.rand,
            )
        )

    def respawn_robot(self):
        if self.task == "ascent" or self.task == "flat":
            ground = "ground"
        if self.task == "descent":
            ground = "floor"
        self.robot_spawn.call(RobotSpawnRequest(
            place=ground,
            task=self.task,
            rand=self.rand,
        ))

    def return_robot_to_initial_state(self):
        _ = self.robot_reset.call(TriggerRequest())

    def spawn_goal(self):
        self.env_gen.call(
            EnvGenRequest(
                action="delete",
                model="goal",
                props=self.task + "_" + self.rand,
            )
        )
        rospy.sleep(0.1)
        self.env_gen.call(
            EnvGenRequest(
                action="generate",
                model="goal",
                props=self.task + "_" + self.rand,
            )
        )

    def create_new_rollout(self):
        self.new_rollout.call(
            NewRolloutRequest(
                experiment=self.experiment,
                seq=self.seq,
                time_step_limit=self.time_step_limit,
                sensors=self.sensors_info,
                angular=self.angular_is_used,
                arm=self.arm_is_used,
                use_penalty_angular=self.penalty_angular,
                use_penalty_deviation=self.penalty_deviation,
            )
        )

    def reset(self, goal=""):
        self.seq += 1
        self.return_robot_to_initial_state()
        self.regenerate_obstacles()
        self.respawn_robot()
        self.spawn_goal()
        self.create_new_rollout()
        self.start_rollout.call(TriggerRequest())
        return self.get_transformed_state()

    def step(self, action):
        self.update_action(action)
        self.pub_robot_cmd.publish(self.action)
        rospy.sleep(TrainingEnv.ACTION_TIME)
        step_return = self.step_return.call(StepReturnRequest())
        reward = step_return.reward
        done = step_return.done
        return self.get_transformed_state(), reward, done, {}

    def render(self, mode='human'):
        pass
