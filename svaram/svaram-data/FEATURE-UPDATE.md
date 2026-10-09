# Feature update: Shared participants and study sessions

Status: Proposed. This document defines the work. The apps do not yet implement it.

## Purpose

Connect the survey app and H10 recorder through one study registry.
Give each answer and recording a link to the correct participant and visit.
Keep study data local. Keep application source in Git.

The first version uses one computer. Both apps use the same local data root.
The default data root is `svaram-data/data/`, relative to the repository root.
The survey app source remains outside this data folder.

## Current behaviour

- The [baseline form](../participant-baseline.html) requires manually entered codes.
- The form saves a browser draft, creates a folder in /svaram-data, and downloads a JSON file.
- The [H10 recorder](../svaram-h10/h10rig/storage.py) creates its own session ID.
- H10 accepts an optional participant code. It does not check a study registry.
- H10 saves independent chunks every five seconds and supports recovery.

## Operator procedure

1. Open the survey app.
2. Select a participant, or create a participant with a unique study code.
3. Create a study session for that participant.
4. Complete and submit the questionnaire before the session.
5. Open H10. Select the shared data root during initial setup.
6. Select a participant and a ready study session from the registry.
7. Start recording. The app creates the destination folder automatically.
8. Add time marks for resting baseline, sound experience and recovery.
9. Stop and save the recording.
10. Complete and submit the questionnaire after the session.

The app supplies the codes to the questionnaire. The participant does not type them.
H10 locks the selected session while recording. A restart creates a new recording.
Simulation remains available and must be clearly marked in stored records and lists.

## Records

| Record | Required information |
| --- | --- |
| Participant | Immutable ID, unique display code, creation time |
| Study session | Immutable ID, participant ID, visit number, protocol version, status |
| Assessment | ID, session ID, pre/post type, questionnaire ID and version, revision, answers, answer times, submission time |
| Questionnaire definition | Questionnaire ID, version, questions, response meanings |
| Recording | ID, session ID, device ID, relative folder path, start/end times, status, simulation flag |
| Phase marker | ID, session ID, recording ID, phase, recording-relative time, UTC reference |

Use UUIDs for internal IDs. Use codes such as `P-001` for display.
One participant can have many study sessions. One session can have many recordings.
Enforce unique participant codes and unique visit numbers within each participant.
Use foreign keys to prevent links to missing records.

Keep study-session status separate from recording status.
Proposed study-session states are draft, ready, in progress, complete and cancelled.
Submission of the pre-session assessment makes the session ready. Skipped optional
answers do not prevent submission. A stopped recording does not complete the visit.
Completion requires a submitted post-session assessment and a saved or recovered
recording. Flag recovered recordings for review. Do not silently treat them as complete capture.

## Storage

Use SQLite for the registry and survey responses. Use separate files for signals and audio.

```text
svaram-data/
  FEATURE-UPDATE.md
  <survey application source>
  data/                         # Excluded from Git
    study.sqlite
    recordings/
      <study-session-id>/
        <recording-id>/
          manifest.json
          raw/
          events/
          derived/
    audio/
    exports/
    backups/
```

Store recording locations relative to the data root. Moving the complete data root
must not break links. Store the study-session ID and recording ID in each manifest.
Keep the manifest as the source for acquisition details and integrity checks.
Keep SQLite as the source for participants, study sessions and assessments.

Store each assessment's answers as compact JSON in SQLite. Store fields used for
selection in separate columns. Index sessions by participant and status, assessments
by session and type, and recordings by session. Store each questionnaire definition
once per version. Preserve unanswered values as null. Zero is a valid answer.

Use short transactions, foreign keys, a bounded busy timeout and WAL mode on local disk.
Do not put database writes in the sensor callback. Handle a busy database visibly.
Both apps must use the same schema version and shared registry access code.
Reject unsupported schema versions before writing.

Save survey drafts after a short delay following an edit. Show save failures.
Submission creates a preserved assessment revision. Corrections create a new revision.
Record the questionnaire version and answer times with each revision.

Keep five-second signal chunks and the existing integrity checks in the first version.
Reconcile committed recordings with the registry after an interrupted write.
Do not delete valid chunks during recovery. Use SQLite's backup facility for database
backups; copying only the main database file while WAL is active is insufficient.

## Questionnaire changes

Adapt the existing baseline form for direct saving through a local application backend.
Do not use browser drafts as the study's authoritative records.
Keep participant context, recent health and immediate state distinct.
Repeat comparable immediate-state questions after the session. Add experience questions.
Do not present past-week responses as immediate pre/post outcomes.
Keep survey baseline separate from resting physiological baseline.
Review the post-session question set against the study protocol before implementation.

## H10 changes

Replace manual participant entry for study recordings with a participant and session list.
Show participant code, visit number, pre-survey status and recording status.
Resolve the destination from the selected session. Confirm that the destination is writable
before acquisition. If the registry is unavailable, show an error before a new study recording.
An active recording must continue saving chunks if a later registry update fails.

Keep existing recordings readable. Do not infer a study-session link from a participant code
alone. Leave old recordings unlinked until an operator explicitly assigns them.

## Git exclusions

The root [.gitignore](../.gitignore) excludes the complete runtime data folder.
It also excludes legacy recording folders, database sidecar files and baseline JSON downloads.
Survey source, schema migrations, question definitions and synthetic test fixtures remain in Git.
All new app-generated data must use the configured data root.
Ignore rules do not protect arbitrary data files saved elsewhere or remove tracked history.

## Acceptance checks

- Create two visits for one participant. Each visit has separate assessments and recordings.
- Submit a pre-session assessment. H10 shows the correct visit as ready.
- Restart acquisition. Both recording attempts remain linked to the same visit.
- Submit a post-session assessment. It links to the same visit as the pre-session assessment.
- Skip a question and answer another with zero. Both values retain their meanings.
- Restart both apps. Submitted answers, drafts and recording links remain available.
- Interrupt a recording write. Recovery preserves committed chunks and repairs registry links.
- Simulate a database lock or disk error. The app reports the failure without false save success.
- Move the complete data root. Relative recording paths still resolve.
- Open an old H10 recording without changing its raw data.
- Verify that Git excludes runtime data and includes application source and migrations.
- Measure registry lookup, survey save latency and disk use with representative study data.

## Delivery order

1. Add the registry schema and shared access code.
2. Add participant selection, session creation and direct survey saving.
3. Add H10 session selection, destination resolution and recording links.
4. Add phase marks, the post-session form and session completion checks.
5. Verify recovery, old-recording access and Git exclusions.

Compressed binary signal storage is a later update. Measure size, read speed, write speed
and recovery before replacing the current format. Tablet access and remote APIs are outside
the first version.
