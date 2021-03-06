import torch
import torch.nn as nn
import torch.nn.functional as F

"""
    MLP Layer used after graph vector representation
"""
from layers.linear_bayesian_layer import BayesianLinear

class MLPReadout(nn.Module):

    def __init__(self, input_dim, output_dim, bias=False, L=2): #L=nb_hidden_layers
        super().__init__()
        list_FC_layers = [ BayesianLinear( input_dim, input_dim, bias=bias ) for l in range(L) ]
        list_FC_layers.append(BayesianLinear( input_dim, output_dim , bias=bias ))
        self.FC_layers = nn.ModuleList(list_FC_layers)
        self.L = L
        
    def forward(self, x):
        y = x
        for l in range(self.L):
            y = self.FC_layers[l](y)
            y = F.relu(y)
        y = self.FC_layers[self.L](y)
        return y
