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
   whole history on every push and pull request, and a second job runs Betterleaks over the
   whole history (next section). On a pull request the scanner, its rules, the Betterleaks
   wrapper and its report template are taken from the base branch (`refs/remotes/origin/<base>`,
   by its full name, so a tag with a similar name cannot stand in for it; a missing base branch
   fails the job), so a change cannot weaken the check that judges it.

Also turn on secret scanning with push protection for the repository on GitHub.

## Second scan: Betterleaks

Two scanners run side by side. `tools/privacy_scan.py` knows this repository's own rules:
addresses, home paths, emails, hostnames, image metadata, and the private patterns on your machine.
[Betterleaks](https://github.com/betterleaks/betterleaks) is an offline secret scanner with a broad
rule set for provider keys and tokens, and it also looks at entropy. Neither replaces the other; a
change must pass both. Betterleaks never prints a value (`--redact`), a finding is one line,
`rule path:line commit`.

[`tools/secret_scan.sh`](tools/secret_scan.sh) wraps it, and the hooks call it after the privacy scan:

| Hook | Runs | Looks at |
| --- | --- | --- |
| `pre-commit` | `sh tools/secret_scan.sh staged` | what the next commit adds |
| `commit-msg` | `sh tools/secret_scan.sh message "$1"` | the commit message, every line (`#` lines included) |
| `pre-push` | `sh tools/secret_scan.sh history` | every commit diff on any ref, plus all commit and tag messages |

Install it once per machine: `brew install betterleaks`, or a release from
https://github.com/betterleaks/betterleaks/releases. Without it on `PATH` the hooks fail (exit 3)
and say so: a guard that skips itself is no guard. CI installs the pinned release 1.10.1, checks its
SHA-256 and runs `sh tools/secret_scan.sh history` and the guard tests. Live validation is off,
because it would send candidates to the network, and the repository's own Betterleaks config is not
loaded.

The guard tests are in `guard_tests/`, not in `tests/`: they need Betterleaks, and `tests/` also runs
in jobs that do not have it. Run them locally, with Betterleaks installed, from the repository root:

```bash
python3 -B -m unittest discover -s guard_tests
```

Each test builds a throwaway repository with the real hooks and drives it with real git commands.
The fake secrets are built at run time and never written to this repository.

A false positive, checked on 1.10.1:

- Put `betterleaks:allow` (or `gitleaks:allow`) in a comment on the same line. This also works for a
  commit message line.
- Or list its fingerprint, one per line, in `.betterleaksignore` at the repository root: build it
  from the finding as `path:rule:line` for staged files, and `commit:path:rule:line` for history
  (the full commit hash from the finding line). The file is committed, so the exception is reviewed.
- A secret that sits in an already pushed commit message cannot be allowed with a comment. Revoke
  it, then decide with the owner whether history is rewritten.

## Private names

Names that are private to a maintainer go in a rules file outside the repository, in the
format of `tools/privacy_rules.json`: `~/.config/agent-oversight/private_rules.json`, or
the path in `AGENT_OVERSIGHT_PRIVATE_RULES`. CI knows only the built-in rules.

A clean scan is necessary and not sufficient. Read the whole diff before you commit.
