ALTER TABLE "user_preferences" DISABLE ROW LEVEL SECURITY;--> statement-breakpoint
DROP POLICY "user_preferences_select" ON "user_preferences" CASCADE;--> statement-breakpoint
DROP POLICY "user_preferences_insert" ON "user_preferences" CASCADE;--> statement-breakpoint
DROP POLICY "user_preferences_update" ON "user_preferences" CASCADE;--> statement-breakpoint
DROP POLICY "user_preferences_delete" ON "user_preferences" CASCADE;