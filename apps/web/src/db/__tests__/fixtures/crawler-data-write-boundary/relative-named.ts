import { db } from "@/db";
import { jobPosting } from "../../../schema";

void db.insert(jobPosting);
