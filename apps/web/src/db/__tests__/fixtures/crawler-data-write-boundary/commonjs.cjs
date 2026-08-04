/* eslint-disable @typescript-eslint/no-require-imports */
const { db } = require("@/db");
const schema = require("../../../schema");

void db.update(schema.jobPosting);
