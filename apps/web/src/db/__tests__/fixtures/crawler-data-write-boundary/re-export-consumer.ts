import { db } from "@/db";
import { crawlerTable } from "./re-export";

void db.insert(crawlerTable);
