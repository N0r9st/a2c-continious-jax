import functools
import os
os.environ['XLA_PYTHON_CLIENT_PREALLOCATE'] = 'false'
from copy import deepcopy
import time
import jax
import jax.numpy as jnp
import numpy as np
import wandb
import itertools
import multiprocessing as mp

from jax_a2c.a2c import p_step
from jax_a2c.q_updates import q_step, train_test_split, test_qf, train_test_split_k_repeat, general_train_test_split
from jax_a2c.distributions import sample_action_from_normal as sample_action
from jax_a2c.env_utils import make_vec_env, DummySubprocVecEnv, run_workers
from jax_a2c.evaluation import eval, q_eval
from jax_a2c.policy import DiagGaussianPolicy, QFunction, DiagGaussianStateDependentPolicy
from jax_a2c.utils import (Experience, collect_experience, create_train_state, select_random_states,
                           process_experience, concat_trajectories, process_base_rollout_output,
                           stack_experiences, process_rollout_output,  process_mc_rollout_output,
                           calculate_interactions_per_epoch)
from jax_a2c.km_mc_traj import km_mc_rollouts
from jax_a2c.saving import save_state, load_state
from flax.core import freeze
from stable_baselines3.common.vec_env.vec_normalize import VecNormalize
from multihost.job_server import KLMJobServer

POLICY_CLASSES = {
    'DiagGaussianPolicy': DiagGaussianPolicy, 
    'DiagGaussianStateDependentPolicy': DiagGaussianStateDependentPolicy,
}
def _policy_fn(prngkey, observation, params, apply_fn, determenistic=False):
    values, (means, log_stds) = apply_fn({'params': params}, observation)
    sampled_actions  = means if determenistic else sample_action(prngkey, means, log_stds)
    return values, sampled_actions

def _worker(remote, k_remotes, parent_remote, spaces, device, add_args) -> None:
    port = 6951 
    server = KLMJobServer(host='*', port=port, password='fuckingpassword')

    print('D:', device)
    os.environ['CUDA_VISIBLE_DEVICES'] = str(device)
    parent_remote.close()
    k_envs = DummySubprocVecEnv(remotes=k_remotes)
    
    k_envs.observation_space, k_envs.action_space = spaces
    k_envs = VecNormalize(k_envs, training=False)
    km_mc_rollouts_ = functools.partial(km_mc_rollouts, k_envs=k_envs)

    _policy_fn = jax.jit(add_args['policy_fn'], static_argnames=('determenistic',))
    print("WORKER STARTED")
    while True:
        try:
            args = server.get_job()
            iteration = args.pop('iteration')
            print(f'GOT JOB FROM {iteration} ITERATION')
            k_envs.obs_rms = args.pop('train_obs_rms')
            k_envs.ret_rms = args.pop('train_ret_rms')
            policy_fn = functools.partial(_policy_fn, **(args.pop('policy_fn')))
            mc_oar = km_mc_rollouts_(policy_fn=policy_fn, **args)
            result = dict(
                iteration=iteration,
                mc_oar=mc_oar,
            )
            server.commit_result(result)
            print('COMMITED RESULT')
        except EOFError:
            break

def main(args: dict):
    args['async'] = True
    if not args['split_between_devices']:
        os.environ['CUDA_VISIBLE_DEVICES'] = args['device']

    num_transition_steps = args['num_timesteps']//(args['num_envs'] * args['num_steps'])

    envs = make_vec_env(
        name=args['env_name'], 
        num=2, 
        norm_r=args['norm_r'], 
        norm_obs=args['norm_obs'],
        ctx=ctx)
        
    k_envs_fn = functools.partial(make_vec_env,
        name=args['env_name'], 
        num=args['num_k_envs'], 
        norm_r=args['norm_r'], 
        norm_obs=args['norm_obs'],
        ctx=ctx
        )
    policy_model = POLICY_CLASSES[args['policy_type']](
        hidden_sizes=args['hidden_sizes'], 
        action_dim=envs.action_space.shape[0],
        init_log_std=args['init_log_std'])

    qf_model = QFunction(hidden_sizes=args['q_hidden_sizes'], action_dim=envs.action_space.shape[0],)

    prngkey = jax.random.PRNGKey(args['seed'])

    state = create_train_state(
        prngkey,
        policy_model,
        qf_model,
        envs,
        learning_rate=args['lr'],
        decaying_lr=args['linear_decay'],
        max_norm=args['max_grad_norm'],
        decay=args['rms_beta2'],
        eps=args['rms_eps'],
        train_steps=num_transition_steps
    )

    _apply_policy_fn = functools.partial(_policy_fn, apply_fn=state.apply_fn, determenistic=False)
    
    # -----------------------------------------
    #            STARTING WORKERS
    #-----------------------------------------
    if args['num_workers'] is not None:
        add_args = {'policy_fn': _apply_policy_fn}
        remotes = run_workers(
            _worker, 
            k_envs_fn, 
            args['num_workers'], 
            (envs.observation_space, envs.action_space),
            ctx,
            split_between_devices=args['split_between_devices'],
            add_args=add_args)
    # ------------------------------------------

    while True:
        time.sleep(5)
    
if __name__=='__main__':

    from args import args

    ctx = mp.get_context("forkserver")

    main(args)