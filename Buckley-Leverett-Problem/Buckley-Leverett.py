"""
Solving the Buckley-Leverett Problem using W-PINNs-DE

            u_t + (F(u))_x = 0, for (x,t) in (-1,1)x(0,1.5)
                    u(x,0) = 1 for -0.5 <= x <= 0.0
                    u(x,0) = 0 otherwise

where F(u) = (u^2)/(4u^2 + (1-u)^2)

The Neural Network is constructed as follows:
                                                         ( sigma(t,x,theta) )

                       ( t )                             ( sigma(t,x,theta) )                          (          )
             Input:            ----> Activation Layers:          .               ----> Output Layer:   (  u(t,x)  )
                       ( x )                                     .                                     (          )
                                                                 .
                                                         ( sigma(t,x,theta) )
"""
# Import libraries
import torch
import torch.nn as nn
import numpy as np
import time
import scipy.io

# Seeds
torch.manual_seed(123456)
np.random.seed(123456)

# Generate Neural Network
class DNN(nn.Module):

    def __init__(self):
        super(DNN, self).__init__()
        self.net = nn.Sequential()                                                 # Define neural network
        self.net.add_module('Linear_layer_1', nn.Linear(2, 30))                    # First linear layer
        self.net.add_module('Tanh_layer_1', nn.Tanh())                             # First activation Layer

        for num in range(2, 7):                                                    # Number of layers (2 through 7)
            self.net.add_module('Linear_layer_%d' % (num), nn.Linear(30, 30))      # Linear layer
            self.net.add_module('Tanh_layer_%d' % (num), nn.Tanh())                # Activation Layer
        self.net.add_module('Linear_layer_final', nn.Linear(30, 1))                # Output Layer

    def forward(self, x):
        return self.net(x)

    # Loss function for PDE
    def loss_pde(self, x):
        u = self.net(x)
        # Gradients and partial derivatives
        du_g = gradients(u, x)[0]                                  # Gradient [u_t, u_x]
        u_t, u_x = du_g[:, :1], du_g[:, 1:]                        # Partial derivatives u_t, u_x
        F = (u**2)/(4*(u**2) + (1-u)**2)
        DF = gradients(F, x)[0]                                    # Gradient of flux DF
        F_x = DF[:, 1:]                                            # Partial derivativEe of flux, F(u)_x

        # Loss function for the Euler Equations
        f = ((u_t + F_x)**2).mean()
        return f

    # Loss function for initial condition
    def loss_ic(self, x_ic, u_ic):
        y_ic = self.net(x_ic)                                      # Initial condition
        u_ic_nn = y_ic[:, 0]

        # Loss function for the initial condition
        loss_ics = ((u_ic_nn - u_ic) ** 2).mean()
        return loss_ics


# Calculate gradients using torch.autograd.grad
def gradients(outputs, inputs):
    return torch.autograd.grad(outputs, inputs,grad_outputs=torch.ones_like(outputs), create_graph=True)

# Convert torch tensor into np.array
def to_numpy(input):
    if isinstance(input, torch.Tensor):
        return input.detach().cpu().numpy()
    elif isinstance(input, np.ndarray):
        return input
    else:
        raise TypeError('Unknown type of input, expected torch.Tensor or ' \
                        'np.ndarray, but got {}'.format(type(input)))

# Initial conditions
def IC(x):
    N = len(x)
    u_init = np.zeros((x.shape[0]))
    for i in range(N):
        if (-0.5 <= x[i] and x[i] <= 0):
            u_init[i] = 1
        else:
            u_init[i] = 0
    return u_init

# Solve Euler equations using PINNs
def main():
    # Initialization
    device = torch.device('cpu')                                          # Run on CPU
    lr = 0.0005                                                           # Learning rate
    num_x = 1000                                                          # Number of points in t
    num_t = 1000                                                          # Number of points in x
    num_i_train = 1000                                                    # Random sampled points from IC
    epochs = 49911                                                        # Number of iterations
    num_f_train = 11000                                                   # Random sampled points in interior
    x = np.linspace(-2.625, 2.5,num_x)                                    # Partitioned spatial axis
    t = np.linspace(0, 1.5, num_t)                                        # Partitioned time axis
    t_grid, x_grid = np.meshgrid(t, x)                                    # (t,x) in [0,0.2]x[a,b]
    T = t_grid.flatten()[:, None]                                         # Vectorized t_grid
    X = x_grid.flatten()[:, None]                                         # Vectorized x_grid

    xs = np.linspace(-1, 1, num_x)                                         # Partitioned spatial axis
    ts = np.linspace(0, 1.5, num_t)                                       # Partitioned time axis
    ts_grid, xs_grid = np.meshgrid(ts, xs)                                # (t,x) in [0,0.2]x[a,b]
    Ts = ts_grid.flatten()[:, None]                                       # Vectorized t_grid
    Xs = xs_grid.flatten()[:, None]

    id_ic = np.random.choice(num_x, num_i_train, replace=False)           # Random sample numbering for IC
    id_f = np.random.choice(num_x*num_t, num_f_train, replace=False)      # Random sample numbering for interior

    x_ic = x_grid[id_ic, 0][:, None]                                      # Random x - initial condition
    t_ic = t_grid[id_ic, 0][:, None]                                      # random t - initial condition
    x_ic_train = np.hstack((t_ic, x_ic))                                  # Random (x,t) - vectorized
    u_ic_train = IC(x_ic)                       # Initial condition evaluated at random sample

    x_int = X[:, 0][id_f, None]                                           # Random x - interior
    t_int = T[:, 0][id_f, None]                                           # Random t - interior
    x_int_train = np.hstack((t_int, x_int))                               # Random (x,t) - vectorized
    x_test = np.hstack((Ts, Xs))                                          # Vectorized whole domain

    # Generate tensors
    x_ic_train = torch.tensor(x_ic_train, dtype=torch.float32).to(device)
    x_int_train = torch.tensor(x_int_train, requires_grad=True, dtype=torch.float32).to(device)
    x_test = torch.tensor(x_test, dtype=torch.float32).to(device)

    u_ic_train = torch.tensor(u_ic_train, dtype=torch.float32).to(device)

    # Initialize neural network
    model = DNN().to(device)

    # Loss and optimizer
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    # Train PINNs
    def train(epoch):
        model.train()
        def closure():
            optimizer.zero_grad()                                                     # Optimizer
            loss_pde = model.loss_pde(x_int_train)                                    # Loss function of PDE
            loss_ic = model.loss_ic(x_ic_train,u_ic_train)   # Loss function of IC
            loss = 0.1*loss_pde + 10*loss_ic                                          # Total loss function G(theta)

            # Print iteration, loss of PDE and ICs
            print(f'epoch {epoch} loss_pde:{loss_pde:.8f}, loss_ic:{loss_ic:.8f}')
            loss.backward()
            return loss

        # Optimize loss function
        loss = optimizer.step(closure)
        loss_value = loss.item() if not isinstance(loss, float) else loss
        # Print total loss
        print(f'epoch {epoch}: loss {loss_value:.6f}')

    # Print CPU
    print('Start training...')
    tic = time.time()
    for epoch in range(1, epochs+1):
        train(epoch)
    toc = time.time()
    print(f'Total training time: {toc - tic}')

    # Evaluate on the whole computational domain
    u_pred = to_numpy(model(x_test))
    scipy.io.savemat('Sod_Shock_Tube.mat', {'x': xs, 't': ts,'u': u_pred[:,0]})

if __name__ == '__main__':
    main()
