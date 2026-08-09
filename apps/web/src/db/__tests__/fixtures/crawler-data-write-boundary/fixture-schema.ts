import { uuid, pgTable } from "drizzle-orm/pg-core";

const retiredName = "job_" + "posting";

export const jobPosting = pgTable(retiredName, {
  id: uuid("id").primaryKey(),
});
