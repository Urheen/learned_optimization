---
jupytext:
  formats: ipynb,md:myst
  text_representation:
    extension: .md
    format_name: myst
    format_version: 0.13
    jupytext_version: 1.13.5
kernelspec:
  display_name: Python 3
  name: python3
---

+++ {"id": "moshuXIJI2v8"}

# Part 2: Custom Tasks, Task Families, and Performance Improvements

In this part, we will look at how to define custom tasks and datasets. We will also consider _families_ of tasks, which are common specifications of meta-learning problems. Finally, we will look at how to efficiently parallelize over tasks during training.

+++ {"id": "FyfbHOeU2RL8"}

## Prerequisites

This document assumes knowledge of JAX which is covered in depth at the [JAX Docs](https://jax.readthedocs.io/en/latest/index.html).
In particular, we would recomend making your way through [JAX tutorial 101](https://jax.readthedocs.io/en/latest/jax-101/index.html). We also recommend that you have worked your way through Part 1.

```{code-cell}
:id: poKoG6orBYNi

!pip install git+https://github.com/google/learned_optimization.git
```

```{code-cell}
:id: HHYLexWR2BZK

import numpy as np
import jax.numpy as jnp
import jax
from matplotlib import pylab as plt

from learned_optimization.outer_trainers import full_es
from learned_optimization.outer_trainers import truncated_pes
from learned_optimization.outer_trainers import gradient_learner
from learned_optimization.outer_trainers import truncation_schedule

from learned_optimization.tasks import quadratics
from learned_optimization.tasks import fixed_mlp
from learned_optimization.tasks import base as tasks_base
from learned_optimization.tasks.datasets import base as datasets_base

from learned_optimization.learned_optimizers import base as lopt_base
from learned_optimization.learned_optimizers import mlp_lopt
from learned_optimization.optimizers import base as opt_base

from learned_optimization import optimizers
from learned_optimization import eval_training

import haiku as hk
import tqdm
```

+++ {"id": "Y4foHdLSqkBs"}

## Defining a custom Dataset

The dataset's in this library consists of iterators which yield batches of the corresponding data. For the provided tasks, these dataset have 4 splits of data rather than the traditional 3. We have "train" which is data used by the task to train a model, "inner_valid" which contains validation data for use when inner training (training an instance of a task). This could be use for, say, picking hparams. "outer_valid" which is used to meta-train with -- this is unseen in inner training and thus serves as a basis to train learned optimizers against. "test" which can be used to test the learned optimizer with.

To make a dataset, simply write 4 iterators with these splits.

For performance reasons, creating these iterators cannot be slow.
The existing dataset's make extensive use of caching to share iterators across tasks which use the same data iterators.
To account for this reuse, it is expected that these iterators are always randomly sampling data and have a large shuffle buffer so as to not run into any sampling issues.

```{code-cell}
:id: IpgZo8RoqmE1

import numpy as np

@datasets_base.dataset_lru_cache
def data_iterator():
  bs = 3
  while True:
    batch = {"data": np.zeros([bs, 5])}
    yield batch
  
def get_datasets():
  return datasets_base.Datasets(train=data_iterator(),
                           inner_valid=data_iterator(),
                           outer_valid=data_iterator(),
                           test=data_iterator())

ds = get_datasets()
next(ds.train)
```

+++ {"id": "l6aOBXMhQ_Pe"}

## Defining a custom `Task`

To define a custom class, one simply needs to write a base class of `Task`. Let's look at a simple task consisting of a quadratic task with noisy targets.

```{code-cell}
:id: oyKqzLvnnyhs

# First we construct data iterators.
def noise_datasets():
  def _fn():
    while True:
      yield np.random.normal(size=[4, 2]).astype(dtype=np.float32)

  return datasets_base.Datasets(train=_fn(), inner_valid=_fn(),
                                outer_valid=_fn(), test=_fn())

class MyTask(tasks_base.Task):
  datasets = noise_datasets()

  def loss(self, params, state, rng, data):
    return jnp.sum(jnp.square(params - data)), None

  def init(self, key):
    return jax.random.normal(key, shape=(4, 2)), None

task = MyTask()
key = jax.random.PRNGKey(0)
key1, key = jax.random.split(key)
params, state = task.init(key)

task.loss(params, state, key1, next(task.datasets.train))
```

+++ {"id": "yGOo6ixjhbR-"}

## Meta-training on multiple tasks: `TaskFamily`

What we have shown previously was meta-training on a single task instance.
While sometimes this is sufficient for a given situation, in many situations we seek to meta-train a meta-learning algorithm such as a learned optimizer on a mixture of different tasks.

One path to do this is to simply run more than one meta-gradient computation, each with different tasks, average the gradients, and perform one meta-update.
This works great when the tasks are quite different -- e.g. meta-gradients when training a convnet vs a MLP.
A big negative to this is that these meta-gradient calculations are happening sequentially, and thus making poor use of hardware accelerators like GPU or TPU.

As a solution to this problem, we have an abstraction of a `TaskFamily` to enable better use of hardware. A `TaskFamily` represents a distribution over a set of tasks and specifies particular samples from this distribution as a pytree of jax types.

The function to sample these configurations is called `sample`, and the function to get a task from the sampled config is `task_fn`. `TaskFamily` also optionally contain datasets which are shared for all the `Task` it creates.

As a simple example, let's consider a family of quadratics parameterized by meansquared error to some point which itself is sampled.

```{code-cell}
:id: F4XUCBeRlPe4

PRNGKey = jnp.ndarray
TaskParams = jnp.ndarray

class FixedDimQuadraticFamily(tasks_base.TaskFamily):
  """A simple TaskFamily with a fixed dimensionality but sampled target."""

  def __init__(self, dim: int):
    super().__init__()
    self._dim = dim
    self.datasets = None

  def sample(self, key: PRNGKey) -> TaskParams:
    # Sample the target for the quadratic task.
    return jax.random.normal(key, shape=(self._dim,))

  def task_fn(self, task_params: TaskParams) -> tasks_base.Task:
    dim = self._dim

    class _Task(tasks_base.Task):

      def loss(self, params, state, rng, _):
        # Compute MSE to the target task.
        return jnp.sum(jnp.square(task_params - params)), state

      def init(self, key):
        return jax.random.normal(key, shape=(dim,)), None

    return _Task()
```

+++ {"id": "EO1UBpJYltYc"}

*With* this task family defined, we can create instances by sampling a configuration and creating a task. This task acts like any other task in that it has an `init` and a `loss` function.

```{code-cell}
:id: dhmYO4r3lx5g

task_family = FixedDimQuadraticFamily(10)
key = jax.random.PRNGKey(0)
task_cfg = task_family.sample(key)
task = task_family.task_fn(task_cfg)

key1, key = jax.random.split(key)
params, model_state = task.init(key)
batch = None
task.loss(params, model_state, key, batch)
```

+++ {"id": "QsYxiGvvdX8Y"}

To achive speedups, we can now leverage `jax.vmap` to train *multiple* task instances in parallel! Depending on the task, this can be considerably faster than serially executing them.

```{code-cell}
:id: -xdtw53zmkS7

def train_task(cfg, key):
  task = task_family.task_fn(cfg)
  key1, key = jax.random.split(key)
  params, model_state = task.init(key1)
  opt = opt_base.Adam()

  opt_state = opt.init(params, model_state)

  for i in range(4):
    params, model_state = opt.get_params_state(opt_state)
    (loss, model_state), grad = jax.value_and_grad(task.loss, has_aux=True)(params, model_state, key, None)
    opt_state = opt.update(opt_state, grad, loss, model_state)
  loss, model_state = task.loss(params, model_state, key, None)
  return loss

task_cfg = task_family.sample(key)
print("single loss",  train_task(task_cfg, key))
  
keys = jax.random.split(key, 32)
task_cfgs = jax.vmap(task_family.sample)(keys)
losses = jax.vmap(train_task)(task_cfgs, keys)
print("multiple losses", losses)
```

+++ {"id": "GGkMPurVoUp4"}

Because of this ability to apply vmap over task families, this is the main building block for a number of the high level libraries in this package. Single tasks can always be converted to a task family with:

```{code-cell}
:id: xJtAFmcUofez

single_task = fixed_mlp.FashionMnistRelu32_8()
task_family = tasks_base.single_task_to_family(single_task)
```

+++ {"id": "mFOa2JDZokiy"}

This wrapper task family has no configuable value and always returns the base task.

```{code-cell}
:id: -5D0P1-qoon8

cfg = task_family.sample(key)
print("config only contains a dummy value:", cfg)
task = task_family.task_fn(cfg)
# Tasks are the same
assert task == single_task
```

+++ {"id": "wHBnOXEInnRs"}

## Limitations of `TaskFamily`
Task families are designed for, and only work for variation that results in a static computation graph. This is required for `jax.vmap` to work.

This means things like naively changing hidden sizes, or number of layers, activation functions is off the table.

In some cases, one can leverage other jax control flow such as `jax.lax.cond` to select between implementations. For example, one could make a `TaskFamily` that used one of 2 activation functions. While this works, the resulting vectorized computation could be slow and thus profiling is required to determine if this is a good idea or not.

In this code base, we use `TaskFamily` to mainly parameterize over different kinds of initializations.