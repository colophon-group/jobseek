import { db } from "@/db";
import * as schema from "../../../schema";

void db.update(schema.jobPosting);
