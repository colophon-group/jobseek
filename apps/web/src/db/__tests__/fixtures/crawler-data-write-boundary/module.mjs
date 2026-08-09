import { db } from "@/db";
import * as schema from "../../../schema";

void db.delete(schema.jobPosting);
