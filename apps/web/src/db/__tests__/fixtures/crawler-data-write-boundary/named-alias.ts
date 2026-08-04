import { db } from "@/db";
import { jobPosting as crawlerTable } from "./fixture-schema";

void db.delete(crawlerTable);
