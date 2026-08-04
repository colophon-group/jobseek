import { db } from "@/db";
import * as schema from "./fixture-schema";

void db.update(schema.jobPosting);
