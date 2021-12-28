# coding=utf-8
# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for simple_lopt_train."""

import tempfile

from absl.testing import absltest
from learned_optimization.examples import simple_lopt_train
from learned_optimization.tasks import quadratics


class SimpleLoptTrainTest(absltest.TestCase):

  def test_train(self):
    with tempfile.TemporaryDirectory() as path:
      simple_lopt_train.train(path, 10, task=quadratics.QuadraticTask())


if __name__ == '__main__':
  absltest.main()
