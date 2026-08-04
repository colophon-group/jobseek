import { db } from "@/db";
import { jobPosting } from "./fixture-schema";

const envelope = { target: jobPosting };
void db.update(envelope.target);
