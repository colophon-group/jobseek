declare const supabase: {
  from(table: string): { upsert(value: unknown): unknown };
};

const tableName = "job_" + "posting";
void supabase.from(tableName).upsert({ id: "posting-1" });
