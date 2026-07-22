BEGIN;

-- The UI's default "Add interview" action uses the general
-- `interview` type. The application schema already includes it, but the
-- original database CHECK constraint predates that option.
ALTER TABLE application_interview
  DROP CONSTRAINT IF EXISTS application_interview_type_check;

ALTER TABLE application_interview
  ADD CONSTRAINT application_interview_type_check
  CHECK (
    type IN (
      'interview',
      'phone_screen',
      'video_call',
      'technical',
      'coding',
      'system_design',
      'behavioral',
      'onsite',
      'panel',
      'hiring_manager',
      'other'
    )
  );

-- Privacy is the safe default for every newly created or mirrored
-- watchlist. Existing rows keep their current visibility.
ALTER TABLE watchlist ALTER COLUMN is_public SET DEFAULT false;

COMMIT;
