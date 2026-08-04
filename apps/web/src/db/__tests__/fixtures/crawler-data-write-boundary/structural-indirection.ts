import { db } from "@/db";
import { jobPosting } from "../../../schema";

const envelope = { target: jobPosting };
void db.update(envelope.target);
