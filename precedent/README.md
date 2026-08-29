# Precedent

August builds AI tools that let law firms run their own judgment at scale. One in particular is automation of the legal playbook for streamlining firm-wide approaches to contracts and negotiation. The legal playbook is a longstanding device used by law firms to define house rules, preferred positions, fallback language, and escalation paths for reviewing and handling contracts.  

August makes agents that generate legal playbooks from a firm's past agreements and then compare inbound contracts against the playbooks to offer edits and flag anomalies. A lawyer can point August at a folder of past agreements, give it a new contract, and August extracts what the firm previously pushed for, relevant language, and where it made concessions. After deriving a playbook, it applies the rules and can mark up a draft of the contract for human review. 

Here, you'll build a working miniature of that product: an agent system that
creates a negotiation playbook from a firm's files, then uses that playbook to
review and respond to inbound contracts.

## Your task

This assessment has two stages:

**Stage 1 - create the playbook.** Point your agent at `./corpus`. It contains
the working files of one firm's MSA practice for a software client: the
standard form, executed agreements, negotiation drafts, internal memos, an
approvals log, and an old clause matrix. From those documents alone, your agent
works out how this firm actually negotiates, clause by clause: its starting
position, the fallbacks it has really accepted, the terms it never signs, and
who signs off on what. It writes that playbook to `./playbook/` as a durable
artifact.

**Stage 2 - use it on new contracts.** Your agent receives a counterparty draft and
reviews it against the playbook it generated. For each clause it takes a
position - accept it, counter it with the firm's language, or escalate it to a
lawyer - with the reasoning and the corpus documents that back it. That
per-clause call is the clause's **disposition**, and it is always exactly one
of `accept`, `counter`, or `escalate`.

Questions a reviewing lawyer would expect your system to answer:

> Where does this draft sit against our positions?

> We conceded this point before. Was it approved, and by whom?

> Nothing in our files covers this clause. Who needs to see it?

Keep in mind:

- The documents disagree with each other in places and conflicts should be flagged however makes sense to you. Guidance goes stale, and
  what the firm actually signed, and what its approvers actually approved, is
  evidence of what it accepts. 
- A rule is only as good as the evidence behind it. Every rule in your playbook, and every position your review takes, should cite the files it
  stands on.
- The playbook covers what the corpus covers. A clause your corpus does not
  answer is a real situation with a right answer: escalate it. Escalating
  everything is just as wrong.
- The corpus is the source of truth, not market knowledge. This firm's positions are its own.

How you build this is up to you: one agent or several, any language, any
orchestration, any playbook format. Your system should not be tuned to this
particular firm. Grading runs it against a comparable corpus from a different
firm, with the same folder layout and document kinds but different positions,
and against drafts you have not seen.

## The corpus

    ./corpus/template/    the firm's standard form (TXT)
    ./corpus/deals/       11 executed agreements (TXT, PDF); some counterparties recur
    ./corpus/redlines/    negotiation turns: counterparty draft and the firm's counter, as clean version pairs
    ./corpus/memos/       internal memoranda on positions, approvals, and one declined engagement
    ./corpus/policies/    approvals_log.csv (deviation approvals) and clause_matrix_2023.xlsx (an old position matrix)
    ./inbound/            two counterparty drafts (TXT) to test your system against

## What is provided

- **AI API credentials:** a Base URL and an API key, shown on your Litmus
  assessment page, that work with any OpenAI-compatible client: point the
  client's `baseURL`/`base_url` at that URL and pass the key. Your system must
  read them from the `LITMUS_AI_BASE_URL` and `LITMUS_AI_API_KEY` environment
  variables. Export them in your shell or load them from a `.env` your code
  reads.
- `./validate.sh` - checks your deliverable against the contract below.

## What you deliver

A runnable service.

- An executable `./start` at the package root that can handle whatever your
  system needs to build and run. It serves HTTP on the port in `$PORT`.
- On boot it runs Stage 1: derive the playbook from `./corpus` and write it to
  `./playbook/`, in whatever format serves your design. `GET /` returns 200
  once the service is ready. Boot, including playbook generation, finishes
  within **5 minutes**.
- `POST /api/review` runs Stage 2. Request body:
  `{"contract": "<the full text of the draft>"}`. The response is your review,
  returned within **3 minutes**. Its format is yours to design: JSON, markdown, or structured however a reviewing lawyer is
  best served. 
- Whatever form it takes, your review must identify each clause it is talking
  about (e.g. "8. Limitation of Liability"), make its disposition on that
  clause unmistakable `accept`, `counter` with the language you propose
  instead, or `escalate` and cite the corpus documents behind each position.
  A review that hedges instead of taking a position is wrong even when its
  analysis is right.
- `./playbook/` is part of the deliverable. It is read and evaluated alongside
  your code: its rules, and what they cite, should hold up on their own.

## Hard rules

- Every disposition is exactly one of `accept`, `counter`, `escalate`.
- Every rationale cites documents that exist in `./corpus`, by filename.
- The playbook is derived at startup from whatever `./corpus` contains. Do not
  hardcode positions into your code.
- Reviews are deterministic: the same draft gets the same dispositions on every
  run.


## Notice
Portions of the documents in this workspace are adapted from Common Paper and Bonterms standard agreements (CC BY 4.0), with modifications.

## Submitting

When you are finished, run:

```
litmus submit
```

