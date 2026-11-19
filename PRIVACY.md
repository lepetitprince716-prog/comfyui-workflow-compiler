# Privacy

This repository is public, and so is every commit in its history. It never holds
anything that identifies a person, a machine or a network.

## What never goes in

- IP and MAC addresses, hostnames, tunnel names, private ports.
- Emails, usernames and account handles other than the repository's own, home-directory paths.
- API keys, tokens, cookies, passwords, anything token-shaped.
- Private workflows, prompts, model file names from a private machine, and any canvas
  that was not written for this repository. Test fixtures are hand-written or come from
  public sources with a compatible licence, named in the fixture's folder.

## The guard

1. Local hooks. `tools/privacy_scan.py` is a standard-library scanner. It prints the rule
   that fired and where, never the matched value, and it fails closed. The hooks in
   `.githooks/` run it on staged files and the commit identity before a commit, on the
   commit message, and on the whole history before a push. Turn them on once per clone:

   ```bash
   git config core.hooksPath .githooks
   ```

2. CI. `.github/workflows/ci.yml` runs the tests and the same scan over the tree and the
   whole history on every push and pull request. On a pull request the scanner and its
   rules are taken from the base branch, so a change cannot weaken the check that judges it.

Also turn on secret scanning with push protection for the repository on GitHub.

## Private names

Names that are private to a maintainer go in a rules file outside the repository, in the
format of `tools/privacy_rules.json`: `~/.config/agent-oversight/private_rules.json`, or
the path in `AGENT_OVERSIGHT_PRIVATE_RULES`. CI knows only the built-in rules.

A clean scan is necessary and not sufficient. Read the whole diff before you commit.
