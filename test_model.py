from ast import main
from operator import mod
from tiny_ur5 import TinyUR5Env
import yaml
from initializer import Initializer
import torch
import numpy as np
import skimage
import clip
from skimage import img_as_ubyte
import json
from recorder import NumpyEncoder
import cv2


class TaskSuccess:
    def __init__(self, env: TinyUR5Env, task) -> None:
        self.env = env
        self.task = task

        self.target = task['target']
        self.target_init_pos = env.get_pos_xy(self.target)
        self.target_init_orientation = env.get_pos_orientation(self.target)

        self.task = task['action']
    
    def success(self):
        if self.task == 'push_forward':
            if self.env.get_pos_xy(self.target)[1] - self.target_init_pos[1] > 20:
                return True
        elif self.task == 'push_backward':
            if self.env.get_pos_xy(self.target)[1] - self.target_init_pos[1] < -20:
                return True
        elif self.task == 'push_left':
            if self.env.get_pos_xy(self.target)[0] - self.target_init_pos[0] < -20:
                return True
        elif self.task == 'push_right':
            if self.env.get_pos_xy(self.target)[0] - self.target_init_pos[0] > 20:
                return True
        elif self.task == 'rotate_clock':
            if self.env.get_pos_orientation(self.target) - self.target_init_orientation > 0.3:
                return True
        elif self.task == 'rotate_counterclock':
            if self.env.get_pos_orientation(self.target) - self.target_init_orientation < -0.3:
                return True
        
        return False




class ModelTester:
    def __init__(self, yaml_file, model, model_forward_fn, device, method, show_cv2, show_human, time_upper_bound=500) -> None:
        self.yaml_file = yaml_file
        self.model = model
        self.model_forward_fn = model_forward_fn
        self.device = device
        self.time_upper_bound = time_upper_bound
        self.method = method
        self.show_cv2 = show_cv2
        self.show_human = show_human
    

    def test_1_rollout(self, test_id):
        with open(self.yaml_file, "r") as stream:
            try:
                config = yaml.safe_load(stream)
                # print(config, type(config))
            except yaml.YAMLError as exc:
                print(exc)
        
        initializer = Initializer(config)

        config, task = initializer.get_config_and_task()
        sentence = initializer.get_sentence()
        print(sentence)

        env = TinyUR5Env(render_mode='human', config=config)
        if self.show_human:
            env.render()
        img = env.render('rgb_array')
        if self.show_cv2:
            cv2.imshow('tiny_ur5', cv2.cvtColor(img, cv2.COLOR_RGB2BGR))

        task_success_judge = TaskSuccess(env, task)

        time_step = 0
        while time_step < self.time_upper_bound:
            actions = self.model_forward_fn(env, self.model, sentence, self.method, self.device)
            # for i in range(actions.shape[-1]):
            for i in range(60):
                action = actions[:, i]
                observation, reward, done, info = env.step(action, eef_z=80)
                # env.render()
                img = env.render('rgb_array')
                if self.show_human:
                    env.render()
                if self.show_cv2:
                    cv2.imshow('tiny_ur5', cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
                    cv2.waitKey(1)
                time_step += 1

                success = task_success_judge.success()
                if success:
                    env.close()
                    return True, task

            print(time_step)
        env.close()
        return False, task
    
    def test(self, test_num:int, name):
        tasks_states = []
        for i in range(test_num):
            success, task = self.test_1_rollout(i)
            task['success'] = success
            tasks_states.append(task)
        
        with open(f'results_{name}_{test_num}.json', 'w') as f:
            json.dump(tasks_states, f, cls=NumpyEncoder, indent=4)


def model_forward_fn(env, model, sentence, method, device):
    img = env.render('rgb_array')
    img = img[::-1, :, :3]
    img = skimage.transform.resize(img, (224, 224))
    img = img_as_ubyte(img) / 255
    # skimage.io.imsave('tmp.png', img_as_ubyte(img))
    # img = skimage.io.imread('tmp.png')[::-1,:,:3] / 255
    img = torch.tensor(img, dtype=torch.float32).unsqueeze(0).to(device)
    sentence = clip.tokenize([sentence]).to(device)

    if method == 'bcz':
        phis = torch.tensor(np.linspace(0.0, 1.0, 60, dtype=np.float32)) \
            .unsqueeze(0).unsqueeze(0).repeat(1, 4, 1).to(device)
        joints_trajectory_pred = model(img, sentence, phis)
        return joints_trajectory_pred[0].detach().cpu().numpy()
    elif method == 'ours':

        def _joints_to_sin_cos_(joints):
            sin_cos_joints = [0] * 8
            for i in range(len(joints)):
                sin_cos_joints[i * 2] = np.sin(joints[i])
                sin_cos_joints[i * 2 + 1] = np.cos(joints[i])
            return sin_cos_joints
        
        def _sin_cos_to_joint_(sin, cos):
            angle = np.arctan(sin / cos)
            if cos < 0:
                if sin > 0:
                    angle = angle + np.pi
                else:
                    angle = angle - np.pi
            #     if sin < 0:
            #         angle = -np.pi - angle
            #     elif sin > 0:
            #         angle = np.pi - angle
            # if cos < -0.8 and sin == 0:
            #     angle = -np.pi * 2
            # angle = np.arccos(cos)

            # action_rad[i] = np.arctan(action[0][i * 2] / action[0][i * 2 + 1])
            # if action[0][i * 2 + 1] < 0:
            #     action_rad[i] += np.pi
            # if action_rad[i] < 0:
            #     action_rad[i] += np.pi * 2

            return angle

        def _sin_cos_to_joints_(sin_cos):
            joints = [0] * 4
            for i in range(len(joints)):
                joints[i] = _sin_cos_to_joint_(sin_cos[i * 2], sin_cos[i * 2 + 1])
            return joints
        
        def _seq_sin_cos_to_joint_(sin_cos_seq):
            joints = []
            for i in range(sin_cos_seq.shape[-1]):
                action = _sin_cos_to_joints_(sin_cos_seq[:, i])
                joints.append(action)
            joints = np.transpose(np.array(joints))
            return joints
        
        joint_angles = torch.tensor(_joints_to_sin_cos_(env.robot_joints)).unsqueeze(0).to(device)
        phis = torch.tensor(np.linspace(0.0, 1.0, 60, dtype=np.float32)) \
            .unsqueeze(0).unsqueeze(0).repeat(1, 8, 1).to(device)
        stage = 3
        target_position_pred, ee_pos_pred, \
            displacement_pred, attn_map, attn_map2, \
            attn_map3, attn_map4, joint_angles_traj_pred = \
            model(img, joint_angles, sentence, phis, stage)
        
        action = joint_angles_traj_pred.detach().cpu().numpy()[0]
        action = _seq_sin_cos_to_joint_(action)
        return action


def load_model(ckpt, method, device):
    if method == 'bcz':
        from models.film_model import Backbone
        model = Backbone(img_size=224, num_traces_out=4, embedding_size=256, num_weight_points=10, input_nc=3, device=device)
        model.load_state_dict(torch.load(ckpt, map_location=device)['model'], strict=True)
        # model = model.cpu()
        model = model.to(device)
        return model
    elif method == 'ours':
        from models.backbone_rgbd_sub_attn_tinyur5 import Backbone
        model = Backbone(img_size=224, embedding_size=256, num_traces_out=2, num_joints=8, num_weight_points=12, input_nc=3, device=device)
        model.load_state_dict(torch.load(ckpt, map_location=device)['model'], strict=True)
        model = model.to(device)
        return model


def calculate_success_rate(filename):
    results = json.load(open(filename))

    success = 0
    for i in range(len(results)):
        if results[i]['success'] == True:
            success += 1
    print(success / len(results))

if __name__ == '__main__':
    device = torch.device('cpu')

    # # # BCZ
    # method = 'bcz'
    # ckpt = '/share/yzhou298/ckpts/tinyur5/train-baseline-bcz-film-resnet-huberloss/200000.pth'
    
    # Ours
    method = 'ours'
    # ckpt = '/share/yzhou298/ckpts/tinyur5/train-tinyur5-rgb-sub-attn-range/90000.pth'
    # ckpt = '/share/yzhou298/ckpts/tinyur5/train-tinyur5-rgb-sub-attn-range-larger-dataset/120000.pth'
    ckpt = '/share/yzhou298/ckpts/tinyur5/train-tinyur5-rgb-sub-attn-range-larger-dataset/340000.pth'
    
    
    model = load_model(ckpt, method, device)
    modeltester = ModelTester('config.yaml', model, model_forward_fn, device, method=method, show_cv2=True, show_human=False)
    # modeltester.test_1_rollout(0)
    modeltester.test(100, method)

    # calculate_success_rate('results_ours_100.json')
    calculate_success_rate('results_bcz_100.json')