import { db } from "@/db";
import { jobPosting } from "./fixture-schema";

void db.insert(jobPosting);
