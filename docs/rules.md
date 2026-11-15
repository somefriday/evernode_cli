# Documentation rules

These rules cover the command reference in `docs/`. They exist so that every page answers the same
questions in the same place, for an operator at a terminal and for an agent reading the file.

## Layout

- One page per command: `docs/<group>/<command>.md`. `evernode node create` is `docs/node/create.md`.
  A command without subcommands uses its own name twice: `docs/doctor/doctor.md`.
- `docs/README.md` is the index. It lists every page in one line and gives the command sequences
  that span several commands: setup for each validator type, and maintenance. A sequence is
  commands in order with one line of comment each; what a command does stays on its page. A new
  command is not documented until it is in the index.
- The voting guide, `voting_instruction.md`, stays at the repository root and links here for
  flags. A page never repeats the guide; it links to it.
- `docs/spec/` is the specification: how evernode works inside, for people who change the code.
  It is not part of the command reference and follows its own rules, in
  [spec/rules.md](spec/rules.md).

## The page

Every page has these sections, in this order, with exactly these headings:

1. `# evernode <group> <command>` and one sentence that says what the command is for.
2. `## Example`: the smallest command line that does the usual job. Two lines at most.
3. `## What it does`: one short paragraph in plain words. What changes, on the host or on chain.
4. `## Flags`: one table, or `None.`
5. `## Behaviour`: a short list of what the reader cannot guess from the flags.
6. `## See also`: links to the commands that come before or after. Optional.

A page that needs more than about 40 lines is describing two commands or a guide. Split it or link.

## Examples

- Use `validator01` as the node name, and `sudo` wherever the command needs root.
- An example must run as written, apart from placeholders. Write placeholders in capitals:
  `HASH`, `ADDRESS`, `-1:COLD_OWNER`.
- Never put a seed phrase, a secret key or a real project key in an example.
- Show the common case. Rare flags belong in the table, not in the example.

## Flags table

- Columns, always these four: `Flag`, `Value`, `Default`, `Meaning`.
- Pad the cells so the columns line up in the source file, not only when rendered.
- `Flag`: the exact spelling, short form first: `` `-n`, `--name` ``. A positional argument is
  written in capitals without dashes.
- `Value`: the placeholder (`NAME`, `TOKENS`, `SECONDS`) or the literal choices in backticks. Empty
  for a switch.
- `Default`: the value used when the flag is absent, `required`, or what happens instead (`asked`,
  `menu`, `ask`). Empty when absent simply means "not this".
- `Meaning`: one line, twelve words or fewer, no full stop needed for a fragment. Say what the flag
  changes, not that it is a flag.
- List every flag the parser accepts, in the order a reader needs them: the target first, the
  choice next, `--yes` and `--dry-run` last.

## Behaviour list

Write only what decides whether the command works or what it leaves behind:

- what must be true before it runs (root, a terminal, a setup stage, a synchronized node);
- what it refuses, and the way out;
- what it changes and what it never touches (keys, funds, the firewall);
- exit codes other than 0 for success and 1 for an error;
- what the reader runs next.

Five to eight items. One fact per item. Put the condition before the instruction.

## Wording

- Plain words, present tense, short sentences. Say "the node", "the Safe", "the elector", and use
  the same word for the same thing on every page.
- State the number: "45 tokens", "40 s", "an hour". No "some", "a few", "shortly".
- Describe what the code does today. If the code and a page disagree, one of them has a bug: fix it
  in the same change.
- No history, no rationale longer than a clause, no marketing words.
- Where mainnet and a test network differ (round length, election window), say which one the
  number is for.

## Keeping it true

- Change a command and its page in the same commit. A new flag appears in the parser, the page and
  the index together.
- Every statement on a page must be checkable: against `evernode/arguments.py` for flags, against
  the command's code for behaviour, or by running it on a test network.
- Test what you document. A page for a new command is written after the command has been run, not
  before.
- When a refusal message changes, search `docs/` for the old wording.
