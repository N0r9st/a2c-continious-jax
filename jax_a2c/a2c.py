import functools
from typing import Any, Callable, Dict, Optional, Tuple

import flax
import jax
import jax.numpy as jnp

from jax_a2c.distributions import evaluate_actions_norm as evaluate_actions
from jax_a2c.utils import (PRNGKey, process_experience, process_mc_rollouts,
                           vmap_process_mc_rollouts,
                           vmap_process_rewards_with_entropy, process_experience_with_entropy)

Array = Any

# @functools.partial(jax.jit, static_argnums=(1,6,7,8,9,10,11))
# @functools.partial(jax.jit, static_argnums=(1,6,7))
def loss_fn(
    params: flax.core.frozen_dict, 
    apply_fn: Callable,  
    data_tuple,
    prngkey: PRNGKey,
    q_fn: Callable,
    constant_params,
    ):
    orig_exp, mc_rollouts_exp = data_tuple 
    # mc_rollouts_exp - List[dict], shape (num_workers, L, M*K*(num_samples//num_workers))

    (observations, actions, returns, _) = process_experience_with_entropy(
        orig_exp, 
        apply_fn,
        params['policy_params'],
        lambda_=constant_params['lambda_'], 
        gamma=constant_params['gamma'],
        alpha=constant_params['alpha'],
        )

    mc_rollouts_returns = vmap_process_rewards_with_entropy(
        apply_fn,
        params['policy_params'],
        mc_rollouts_exp['observations'],
        mc_rollouts_exp['actions'],
        mc_rollouts_exp['dones'],
        mc_rollouts_exp['rewards'],
        mc_rollouts_exp['bootstrapped'],
        constant_params['alpha'],
        constant_params['gamma'],
    )

    mc_observations, mc_actions, mc_returns = vmap_process_mc_rollouts(
        mc_rollouts_exp['observations'],
        mc_rollouts_exp['actions'],
        mc_rollouts_returns,
        constant_params['M']
    )
    mc_observations, mc_actions, mc_returns = tuple(map(
        lambda x: x.reshape((x.shape[0]*x.shape[1],) + x.shape[2:]), (mc_observations, mc_actions, mc_returns)
    ))

    observations = jnp.concatenate((observations, mc_observations), axis=0)
    actions = jnp.concatenate((actions, mc_actions), axis=0)
    returns = jnp.concatenate((returns, mc_returns), axis=0)
    returns = jax.lax.stop_gradient(returns)

    action_logprobs, values, dist_entropy, log_stds, action_samples = evaluate_actions(
        params['policy_params'], 
        apply_fn, observations, actions, prngkey)
    advantages = returns - values
    loss_dict = {}

    policy_loss = - (jax.lax.stop_gradient(advantages) * action_logprobs).mean()
    value_loss = ((returns - values)**2).mean()

    q_loss = jnp.array(0)
    if constant_params['q_updates'] is not None:
        q_estimations = q_fn({'params': params['qf_params']}, observations, actions)
        q_loss = ((q_estimations - returns)**2).mean()
        
    if constant_params['q_updates'] == 'rep':
        q_loss += - constant_params['q_loss_coef'] * (
            q_fn(jax.lax.stop_gradient({'params': params['qf_params']}), observations, action_samples).mean() - \
            constant_params['alpha']*log_stds.mean())
    elif constant_params['q_updates'] == 'log':
        sampled_estimations = q_fn({'params': params['qf_params']}, observations, action_samples)
        estimated_advantages = sampled_estimations - values
        q_loss += - (jax.lax.stop_gradient(estimated_advantages) * action_logprobs).mean()

    elif constant_params['q_updates'] == 'rep_only':
        q_loss += - constant_params['q_loss_coef'] * q_fn(
                    jax.lax.stop_gradient({'params': params['qf_params']}), 
                    observations, 
                    action_samples).mean()
        policy_loss=0
        value_loss = 0

    loss = constant_params['value_loss_coef']*value_loss + policy_loss - constant_params['entropy_coef']*dist_entropy + q_loss
    loss_dict.update(
        value_loss=value_loss, 
        policy_loss=policy_loss, 
        dist_entropy=dist_entropy, 
        advantages_max = jnp.abs(advantages).max(),
        min_std=jnp.exp(log_stds).min(),
        q_loss=q_loss
        )
    return loss, loss_dict

# @functools.partial(jax.jit, static_argnums=(3,4,5,6,7))
@functools.partial(jax.jit, static_argnums=(3,))
def step(state, data_tuple, prngkey,
    constant_params):
    
    # observations, actions, returns, advantages = trajectories
    (loss, loss_dict), grads = jax.value_and_grad(loss_fn, has_aux=True)(
        state.params, 
        state.apply_fn, 
        data_tuple,
        prngkey,
        state.q_fn,
        constant_params,)
    new_state = state.apply_gradients(grads=grads)
    return new_state, (loss, loss_dict)
    