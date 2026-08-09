import { db } from "@/db";
import { jobPosting as crawlerTable } from "../../../schema";

void db.insert(crawlerTable);
