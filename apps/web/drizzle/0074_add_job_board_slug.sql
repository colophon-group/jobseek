ALTER TABLE "job_board" ADD COLUMN "board_slug" text;
--> statement-breakpoint
ALTER TABLE "job_board" ADD CONSTRAINT "job_board_board_slug_key" UNIQUE("board_slug");
