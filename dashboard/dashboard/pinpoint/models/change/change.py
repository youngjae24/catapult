# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import collections
import itertools

from dashboard.pinpoint.models.change import commit as commit_module
from dashboard.pinpoint.models.change import patch as patch_module


class Change(collections.namedtuple('Change', ('commits', 'patch'))):
  """A particular set of Commits with or without an additional patch applied.

  For example, a Change might sync to src@9064a40 and catapult@8f26966,
  then apply patch 2423293002.
  """

  def __new__(cls, commits, patch=None):
    """Creates a Change.

    Args:
      commits: An iterable of Commits representing this Change's dependencies.
      patch: An optional Patch to apply to the Change.
    """
    if not commits:
      raise TypeError('At least one commit required.')
    return super(Change, cls).__new__(cls, tuple(commits), patch)

  def __str__(self):
    """Returns an informal short string representation of this Change."""
    string = ' '.join(str(commit) for commit in self.commits)
    if self.patch:
      string += ' + ' + str(self.patch)
    return string

  @property
  def id_string(self):
    """Returns a string that is unique to this set of commits and patch.

    This method treats the commits as unordered. chromium@a v8@b is the same as
    v8@b chromium@a. This is useful for looking up a build with this Change.
    """
    string = ' '.join(commit.id_string for commit in sorted(self.commits))
    if self.patch:
      string += ' + ' + self.patch.id_string
    return string

  @property
  def base_commit(self):
    return self.commits[0]

  @property
  def last_commit(self):
    return self.commits[-1]

  @property
  def deps(self):
    return tuple(self.commits[1:])

  def AsDict(self):
    return {
        'commits': [commit.AsDict() for commit in self.commits],
        'patch': self.patch.AsDict() if self.patch else None,
    }

  @classmethod
  def FromDict(cls, data):
    commits = tuple(commit_module.Commit.FromDict(commit)
                    for commit in data['commits'])
    if 'patch' in data:
      patch = patch_module.Patch.FromDict(data['patch'])
    else:
      patch = None

    return cls(commits, patch=patch)

  @classmethod
  def Midpoint(cls, change_a, change_b):
    """Returns a Change halfway between the two given Changes.

    This function does two passes over the Changes' Commits:
    * The first pass attempts to match the lengths of the Commit lists by
      expanding DEPS to fill in any repositories that are missing from one,
      but included in the other.
    * The second pass takes the midpoint of every matched pair of Commits,
      expanding DEPS rolls as it comes across them.

    A NonLinearError is raised if there is no valid midpoint. The Changes are
    not linear if any of the following is true:
      * They have different patches.
      * Their repositories don't match even after expanding DEPS rolls.
      * The left Change comes after the right Change.
      * They are the same or adjacent.
    See change_test.py for examples of linear and nonlinear Changes.

    Args:
      change_a: The first Change in the range.
      change_b: The last Change in the range.

    Returns:
      A new Change representing the midpoint.
      The Change before the midpoint if the range has an even number of commits.

    Raises:
      NonLinearError: The Changes are not linear.
    """
    if change_a.patch != change_b.patch:
      raise commit_module.NonLinearError(
          'Change A has patch "%s" and Change B has patch "%s".' %
          (change_a.patch, change_b.patch))

    commits_a = list(change_a.commits)
    commits_b = list(change_b.commits)

    _ExpandDepsToMatchRepositories(commits_a, commits_b)
    commits_midpoint = _FindMidpoints(commits_a, commits_b)

    if commits_a == commits_midpoint:
      raise commit_module.NonLinearError('Changes are the same or adjacent.')

    return cls(commits_midpoint, change_a.patch)


def _ExpandDepsToMatchRepositories(commits_a, commits_b):
  """Expands DEPS in a Commit list to match the repositories in another.

  Given two lists of Commits, with one bigger than the other, this function
  looks through the DEPS files for smaller commit list to fill out any missing
  Commits that are already in the bigger commit list.

  Mutates the lists in-place, and doesn't return anything. The lists will not
  have the same size if one Commit list contains a repository that is not found
  in the DEPS of the other Commit list.

  Example:
    commits_a == [chromium@a, v8@c]
    commits_b == [chromium@b]
    This function looks through the DEPS file at chromium@b to find v8, then
    appends that v8 Commit to commits_b, making the lists match.

  Args:
    commits_a: A list of Commits.
    commits_b: A list of Commits.
  """
  # The lists may be given in any order. Let's make commits_b the bigger list.
  if len(commits_a) > len(commits_b):
    commits_a, commits_b = commits_b, commits_a

  # Loop through every DEPS file in commits_a.
  for commit_a in commits_a:
    if len(commits_a) == len(commits_b):
      break
    deps_a = commit_a.Deps()

    # Look through commits_b for any extra slots to fill with the DEPS.
    for commit_b in commits_b[len(commits_a):]:
      dep_a = _FindCommitWithRepository(deps_a, commit_b.repository)
      if dep_a:
        commits_a.append(dep_a)
      else:
        break


def _FindMidpoints(commits_a, commits_b):
  """Returns the midpoint of two Commit lists.

  Loops through each pair of Commits and takes the midpoint. If the repositories
  don't match, a NonLinearError is raised. If the Commits are adjacent and
  represent a DEPS roll, the differing DEPs are added to the end of the lists.

  Args:
    commits_a: A list of Commits.
    commits_b: A list of Commits.

  Returns:
    A list of Commits, each of which is the midpoint of the respective Commit in
    commits_a and commits_b.

  Raises:
    NonLinearError: The lists have a different number of commits even after
      expanding DEPS rolls, a Commit pair contains differing repositories, or a
      Commit pair is in the wrong order.
  """
  commits_midpoint = []

  for commit_a, commit_b in itertools.izip_longest(commits_a, commits_b):
    if not (commit_a and commit_b):
      # If the commit lists are not the same length, bail out. That could happen
      # if commits_b has a repository that was not found in the DEPS of
      # commits_a (or vice versa); or a DEPS roll added or removed a DEP.
      raise commit_module.NonLinearError(
          'Changes have a different number of commits.')

    commit_midpoint = commit_module.Commit.Midpoint(commit_a, commit_b)
    commits_midpoint.append(commit_midpoint)
    if commit_a == commit_midpoint != commit_b:
      # Commits are adjacent.
      # Add any DEPS changes to the commit lists.
      deps_a = commit_a.Deps()
      deps_b = commit_b.Deps()
      commits_a += sorted(
          dep for dep in deps_a.difference(deps_b)
          if not _FindCommitWithRepository(commits_a, dep.repository))
      commits_b += sorted(
          dep for dep in deps_b.difference(deps_a)
          if not _FindCommitWithRepository(commits_b, dep.repository))

  return commits_midpoint


def _FindCommitWithRepository(commits, repository):
  for commit in commits:
    if commit.repository == repository:
      return commit
  return None
