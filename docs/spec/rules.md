# Specification rules

These rules cover `docs/spec/`. The command reference has its own, in [rules.md](../rules.md). Its
wording rules apply here too, with one difference: a property gives its reason, in **Why**.

## What belongs where

Each fact has one home. The other places link to it.

| Fact                                                        | Home                                        |
| ----------------------------------------------------------- | ------------------------------------------- |
| A command's flags, example and what the operator sees       | `docs/<group>/<command>.md`                 |
| A procedure for an operator, in order                       | `docs/README.md`, `voting_instruction.md`   |
| The vote engine's own logic and its checks                  | `vote/README.md`                            |
| The parts of the system and where state lives               | `architecture.md`, `state.md`               |
| A rule several features depend on                           | `properties.md`                             |
| The steps of one feature, its checks and what it leaves     | the feature file                            |

The spec names a flag only where the logic branches on it. The full list is on the command's
page, which each section links to.

## Three kinds of content

- **Architecture and state** describe what exists. They change when a part, a file or a field is
  added, moved or removed.
- **Properties** say what must always be true. Breaking one is a bug, whatever a feature file says.
- **Feature logic** says what a feature does, in order. It may change freely as long as every
  property still holds.

## A property

Every property in `properties.md` has the same five parts:

1. An id as its heading, such as `OP-3`, and a short title in bold on the next line: "A reported
   failure is final." The heading is the id alone, so a link to it never changes:
   `properties.md#op-3`. The prefix names the area. An id is never reused or renumbered; a rule
   that no longer applies stays in the file, marked `withdrawn`, with the commit that withdrew it.
2. **Rule**: what must hold, in a form that can be checked. "Must" and "never" belong here.
3. **Why**: what goes wrong without it. Three sentences at most.
4. **Enforced by**: the file and function where the rule is kept.
5. **Checked by**: the test file that fails when the rule is broken, or "No test".

A property that some code does not keep says so in a line below its five parts. The table at the
top of `properties.md` lists every id with its rule in a line.

Code comments, tests, commit messages and bug reports refer to a property by its id.

## A feature section

Every feature section answers the same questions under the same labels, in this order:

- **Requires**: what must be true before it starts, and what it refuses.
- **Logic**: numbered steps, in the order the code takes them. A step that decides something says
  what each answer leads to.
- **Result**: what is changed on the host and on chain when it succeeds.
- **On failure**: what a failure at each point leaves behind, and how to continue.

A label with nothing to say is left out: a command that only reads has no **On failure**. Each
section starts with the commands it covers, linked to their pages, and the code that does the
work.

A section that needs a property links to it by id and does not restate it.

## Wording

- Describe what the code does today. No history and no plans.
- Plain words, present tense, short sentences. The same word for the same thing everywhere; the
  words are in [glossary.md](glossary.md).
- State the number and its unit: "60 s", "5 tokens", "three sends".
- Where mainnet and a test network differ, say which one the number is for.
- Name code by file and function, never by line number.

## Keeping it true

- A change to behaviour described here changes the spec in the same commit. If the code and the
  spec disagree, one of them has a bug.
- A new feature gets a section. A new rule that more than one feature relies on gets a property.
- Every statement must be checkable: against the named function, against the named test, or by a
  run on a test network. Leave out what cannot be checked.
- A full re-check against the code updates the commit named at the top of [README.md](README.md).
