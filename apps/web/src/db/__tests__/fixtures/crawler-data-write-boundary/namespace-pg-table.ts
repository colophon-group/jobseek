import * as pg from "drizzle-orm/pg-core";

export const retiredPosting = pg.pgTable("job_posting", {
  id: pg.uuid("id").primaryKey(),
});
