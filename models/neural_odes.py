#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@author: borjangeshkovski
"""
##------------#
import torch
import torch.nn as nn
from torchdiffeq import odeint, odeint_adjoint
MAX_NUM_STEPS = 1000

# Hyperparameters
T = 10
time_steps = 15
dt = T/time_steps

# Useful dicos:
activations = {'tanh': nn.Tanh(),
                'relu': nn.ReLU(inplace=True),
                'sigmoid': nn.Sigmoid(),
                'leakyrelu': nn.LeakyReLU(negative_slope=0.25, inplace=True)
}
species = {'inside': -1, 'outside': 0, 'bottleneck': 1}

class Dynamics(nn.Module):
    """
    The nonlinear right hand side $f(u(t), x(t)) of the nODE.
    """
    def __init__(self, device, data_dim, hidden_dim, augment_dim=0, non_linearity='tanh'):
        super(Dynamics, self).__init__()
        self.device = device
        self.augment_dim = augment_dim
        self.data_dim = data_dim
        self.input_dim = data_dim + augment_dim
        self.hidden_dim = hidden_dim
        self.nfe = 0 
        self.non_linearity = activations[non_linearity]
        self.species = species['inside']
        
        if self.species > 0:
            ##-- R^{d_aug} -> R^{d_hid} layer -- 
            blocks1 = [nn.Linear(self.input_dim, hidden_dim) for _ in range(time_steps)]
            self.fc1_time = nn.Sequential(*blocks1)
            ##-- R^{d_hid} -> R^{d_aug} layer --
            blocks3 = [nn.Linear(hidden_dim, self.input_dim) for _ in range(time_steps)]
            self.fc3_time = nn.Sequential(*blocks3)
        else:
            ##-- R^{d_hid} -> R^{d_hid} layer --
            blocks = [nn.Linear(hidden_dim, hidden_dim) for _ in range(time_steps)]
            self.fc2_time = nn.Sequential(*blocks)
        
    def forward(self, t, x):
        """
        The output of the class -> f(x(t), u(t)).
        """
        self.nfe += 1
        k = int(t/dt)
        #In case of MNIST: if t==0: return x.view(x.size(0), -1)

        if self.species < 1:
            w_t = self.fc2_time[k].weight
            b_t = self.fc2_time[k].bias
            if self.species < 0:
                ## w(t)\sigma(x(t))+b(t)
                out = self.non_linearity(x).matmul(w_t.t()) + b_t        
            else:
                ## \sigma(w(t)x(t)+b(t))
                out = self.non_linearity(x.matmul(w_t.t())+b_t)
        else:
            ## w1(t)\sigma(w2(t)x(t)+b2(t))+b1(t)
            w1_t = self.fc1_time[k].weight
            b1_t = self.fc1_time[k].bias
            w2_t = self.fc3_time[k].weight
            b2_t = self.fc3_time[k].bias
            out = self.non_linearity(x.matmul(w1_t.t()) + b1_t)
            out = self.non_linearity(out.matmul(w2_t.t()) + b2_t)
        return out

class Semiflow(nn.Module):
    """
    Given the dynamics f, generate the semiflow by solving x'(t) = f(u(t), x(t))
    """
    def __init__(self, device, dynamics, tol=1e-3, adjoint=False):
        super(Semiflow, self).__init__()
        self.adjoint = adjoint
        self.device = device
        self.dynamics = dynamics
        self.tol = tol

    def forward(self, x, eval_times=None):
        self.dynamics.nfe = 0

        if eval_times is None:
            integration_time = torch.tensor([0, T]).float().type_as(x)
        else:
            integration_time = eval_times.type_as(x)

        if self.dynamics.augment_dim > 0:
            x = x.view(x.size(0), -1)
            aug = torch.zeros(x.shape[0], self.dynamics.augment_dim).to(self.device)
            x_aug = torch.cat([x, aug], 1)
        else:
            x_aug = x

        if self.adjoint:
            out = odeint_adjoint(self.dynamics, x_aug, integration_time, method='euler', options={'step_size': dt})
        else:
            out = odeint(self.dynamics, x_aug, integration_time, method='euler', options={'step_size': dt})
        if eval_times is None:
            return out[1] 
        else:
            return out

    def trajectory(self, x, timesteps):
        integration_time = torch.linspace(0., T, timesteps)
        return self.forward(x, eval_times=integration_time)

class NeuralODE(nn.Module):
    def __init__(self, device, data_dim, hidden_dim, output_dim=2,
                 augment_dim=0, non_linearity='tanh',
                 tol=1e-3, adjoint=False):
        super(NeuralODE, self).__init__()
        #output_dim = 1 pour MSE, 2 pour cross entropy for binary classification..
        self.device = device
        self.data_dim = data_dim
        self.hidden_dim = hidden_dim
        self.augment_dim = augment_dim
        self.output_dim = output_dim
        self.tol = tol

        dynamics = Dynamics(device, data_dim, hidden_dim, augment_dim, non_linearity)
        self.flow = Semiflow(device, dynamics, tol=tol, adjoint=adjoint)
        self.linear_layer = nn.Linear(self.flow.dynamics.input_dim,
                                         self.output_dim)
        self.non_linearity = nn.Tanh()
        
    def forward(self, x, return_features=False):

        cross_entropy = True
        fixed_projector = False
        features = self.flow(x)

        if fixed_projector: 
            import pickle
            with open('text.txt', 'rb') as fp:
                projector = pickle.load(fp)
            pred = features.matmul(projector[-2].t()) + projector[-1]
            self.traj = self.flow.trajectory(x, time_steps)
            self.proj_traj = self.traj.matmul(projector[-2].t()) + projector[-1]

        else:
            self.traj = self.flow.trajectory(x, time_steps)
            pred = self.linear_layer(features)
            self.proj_traj = self.linear_layer(self.traj)
            if not cross_entropy:
                pred = self.non_linearity(pred)
                self.proj_traj = self.non_linearity(self.proj_traj)
        
        if return_features:
            return features, pred
        return pred, self.proj_traj
