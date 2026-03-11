ALTER TABLE "company_request" DROP CONSTRAINT "company_request_resolved_company_id_company_id_fk";
--> statement-breakpoint
ALTER TABLE "company_request" ADD CONSTRAINT "company_request_resolved_company_id_company_id_fk" FOREIGN KEY ("resolved_company_id") REFERENCES "public"."company"("id") ON DELETE set null ON UPDATE no action;