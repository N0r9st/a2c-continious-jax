from typing import Tuple

import flax.linen as nn
import jax
import jax.numpy as jnp

LOG_SIG_MAX = 2
LOG_SIG_MIN = -20

def constant_initializer(bias, dtype=jnp.float_):
    def init(key, shape, dtype=dtype):
        return jnp.ones(shape, jax.dtypes.canonicalize_dtype(dtype)) * bias
    return init

class DiagGaussianPolicy(nn.Module):
    """ Simple MLP-based policy,
    returns: critic's values, (actions' means, actions' log stds)
    """
    hidden_sizes: Tuple[int]
    action_dim: int
    init_log_std: float
    @nn.compact
    def __call__(self, inp):
        x = inp
        for h_size in self.hidden_sizes:
            x = nn.Dense(features=h_size, kernel_init=nn.initializers.orthogonal(scale=jnp.sqrt(2)))(x)
            x = nn.tanh(x)
        values = nn.Dense(
            features=1, 
            kernel_init=nn.initializers.orthogonal(scale=jnp.sqrt(2)), 
            name='Critic_values')(x)

        x = inp
        for h_size in self.hidden_sizes:
            x = nn.Dense(features=h_size, kernel_init=nn.initializers.orthogonal(scale=jnp.sqrt(2)))(x)
            x = nn.tanh(x)
        action_means = nn.Dense(
            features=self.action_dim, name='Actor_means', 
            kernel_init=nn.initializers.orthogonal())(x)
        action_log_stds = self.param('Action_log_stds', constant_initializer(self.init_log_std), (self.action_dim,))
        action_log_stds = jnp.asarray(action_log_stds)
        action_log_stds = jnp.repeat(action_log_stds.reshape(1,-1), axis=0, repeats=x.shape[0])
        action_log_stds = jnp.clip(action_log_stds, a_min=LOG_SIG_MIN, a_max=LOG_SIG_MAX)
        return values, (action_means, action_log_stds)

class QFunction(nn.Module):
    """ MLP-based Q-function approximator
    """
    hidden_sizes: Tuple[int]
    action_dim: int
    @nn.compact
    def __call__(self, obs, actions):
        x = jnp.concatenate([obs, actions], axis=-1) # (bs, o_dim), (bs, a_dim) -> (bs, o_dim+a_dim)
        for h_size in self.hidden_sizes:
            x = nn.Dense(features=h_size, kernel_init=nn.initializers.orthogonal(scale=jnp.sqrt(2)))(x)
            x = nn.tanh(x)
        qvalues = nn.Dense(
            features=1, 
            kernel_init=nn.initializers.orthogonal(scale=jnp.sqrt(2)), 
            name='Q_values')(x)

        return qvalues


class DiagGaussianStateDependentPolicy(nn.Module):
    """ Simple MLP-based policy,
    returns: critic's values, (actions' means, actions' log stds)
    """
    hidden_sizes: Tuple[int]
    action_dim: int
    init_log_std: float
    @nn.compact
    def __call__(self, inp):
        x = inp
        for h_size in self.hidden_sizes:
            x = nn.Dense(features=h_size, kernel_init=nn.initializers.orthogonal(scale=jnp.sqrt(2)))(x)
            x = nn.tanh(x)
        values = nn.Dense(
            features=1, 
            kernel_init=nn.initializers.orthogonal(scale=jnp.sqrt(2)), 
            name='Critic_values')(x)

        x = inp
        for h_size in self.hidden_sizes:
            x = nn.Dense(features=h_size, kernel_init=nn.initializers.orthogonal(scale=jnp.sqrt(2)))(x)
            x = nn.tanh(x)
        action_means = nn.Dense(
            features=self.action_dim, name='Actor_means', 
            kernel_init=nn.initializers.orthogonal())(x)
        # action_log_stds = self.param('Action_log_stds', constant_initializer(self.init_log_std), (self.action_dim,))
        # action_log_stds = jnp.asarray(action_log_stds)
        # action_log_stds = jnp.repeat(action_log_stds.reshape(1,-1), axis=0, repeats=x.shape[0])

        for h_size in self.hidden_sizes:
            x = nn.Dense(features=h_size, kernel_init=nn.initializers.orthogonal(scale=jnp.sqrt(2)))(x)
            x = nn.tanh(x)
        action_log_stds = nn.Dense(
            features=self.action_dim, name='Log_stds', 
            kernel_init=nn.initializers.orthogonal())(x)
        action_log_stds = jnp.clip(action_log_stds, a_min=LOG_SIG_MIN, a_max=LOG_SIG_MAX)
        return values, (action_means, action_log_stds)