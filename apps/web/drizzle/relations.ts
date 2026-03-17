import { relations } from "drizzle-orm/relations";
import { company, followedCompany, user, occupationDomain, occupation, subscription, jobBoard, jobPosting, savedJob, session, companyRequest, account, industry, userPreferences, location, seniority, locationMacroMember, companyDescription, occupationName, seniorityName, occupationDomainName, locationName, industryName } from "./schema";

export const followedCompanyRelations = relations(followedCompany, ({one}) => ({
	company: one(company, {
		fields: [followedCompany.companyId],
		references: [company.id]
	}),
	user: one(user, {
		fields: [followedCompany.userId],
		references: [user.id]
	}),
}));

export const companyRelations = relations(company, ({one, many}) => ({
	followedCompanies: many(followedCompany),
	jobBoards: many(jobBoard),
	companyRequests: many(companyRequest),
	industry: one(industry, {
		fields: [company.industry],
		references: [industry.id]
	}),
	jobPostings: many(jobPosting),
	companyDescriptions: many(companyDescription),
}));

export const userRelations = relations(user, ({many}) => ({
	followedCompanies: many(followedCompany),
	subscriptions: many(subscription),
	savedJobs: many(savedJob),
	sessions: many(session),
	accounts: many(account),
	userPreferences: many(userPreferences),
}));

export const occupationRelations = relations(occupation, ({one, many}) => ({
	occupationDomain: one(occupationDomain, {
		fields: [occupation.domainId],
		references: [occupationDomain.id]
	}),
	occupation: one(occupation, {
		fields: [occupation.parentId],
		references: [occupation.id],
		relationName: "occupation_parentId_occupation_id"
	}),
	occupations: many(occupation, {
		relationName: "occupation_parentId_occupation_id"
	}),
	jobPostings: many(jobPosting),
	occupationNames: many(occupationName),
}));

export const occupationDomainRelations = relations(occupationDomain, ({many}) => ({
	occupations: many(occupation),
	occupationDomainNames: many(occupationDomainName),
}));

export const subscriptionRelations = relations(subscription, ({one}) => ({
	user: one(user, {
		fields: [subscription.userId],
		references: [user.id]
	}),
}));

export const jobBoardRelations = relations(jobBoard, ({one, many}) => ({
	company: one(company, {
		fields: [jobBoard.companyId],
		references: [company.id]
	}),
	companyRequests: many(companyRequest),
	jobPostings: many(jobPosting),
}));

export const savedJobRelations = relations(savedJob, ({one}) => ({
	jobPosting: one(jobPosting, {
		fields: [savedJob.jobPostingId],
		references: [jobPosting.id]
	}),
	user: one(user, {
		fields: [savedJob.userId],
		references: [user.id]
	}),
}));

export const jobPostingRelations = relations(jobPosting, ({one, many}) => ({
	savedJobs: many(savedJob),
	jobBoard: one(jobBoard, {
		fields: [jobPosting.boardId],
		references: [jobBoard.id]
	}),
	company: one(company, {
		fields: [jobPosting.companyId],
		references: [company.id]
	}),
	occupation: one(occupation, {
		fields: [jobPosting.occupationId],
		references: [occupation.id]
	}),
	seniority: one(seniority, {
		fields: [jobPosting.seniorityId],
		references: [seniority.id]
	}),
}));

export const sessionRelations = relations(session, ({one}) => ({
	user: one(user, {
		fields: [session.userId],
		references: [user.id]
	}),
}));

export const companyRequestRelations = relations(companyRequest, ({one}) => ({
	company: one(company, {
		fields: [companyRequest.resolvedCompanyId],
		references: [company.id]
	}),
	jobBoard: one(jobBoard, {
		fields: [companyRequest.resolvedJobBoardId],
		references: [jobBoard.id]
	}),
}));

export const accountRelations = relations(account, ({one}) => ({
	user: one(user, {
		fields: [account.userId],
		references: [user.id]
	}),
}));

export const industryRelations = relations(industry, ({many}) => ({
	companies: many(company),
	industryNames: many(industryName),
}));

export const userPreferencesRelations = relations(userPreferences, ({one}) => ({
	user: one(user, {
		fields: [userPreferences.userId],
		references: [user.id]
	}),
}));

export const locationRelations = relations(location, ({one, many}) => ({
	location: one(location, {
		fields: [location.parentId],
		references: [location.id],
		relationName: "location_parentId_location_id"
	}),
	locations: many(location, {
		relationName: "location_parentId_location_id"
	}),
	locationMacroMembers_countryId: many(locationMacroMember, {
		relationName: "locationMacroMember_countryId_location_id"
	}),
	locationMacroMembers_macroId: many(locationMacroMember, {
		relationName: "locationMacroMember_macroId_location_id"
	}),
	locationNames: many(locationName),
}));

export const seniorityRelations = relations(seniority, ({many}) => ({
	jobPostings: many(jobPosting),
	seniorityNames: many(seniorityName),
}));

export const locationMacroMemberRelations = relations(locationMacroMember, ({one}) => ({
	location_countryId: one(location, {
		fields: [locationMacroMember.countryId],
		references: [location.id],
		relationName: "locationMacroMember_countryId_location_id"
	}),
	location_macroId: one(location, {
		fields: [locationMacroMember.macroId],
		references: [location.id],
		relationName: "locationMacroMember_macroId_location_id"
	}),
}));

export const companyDescriptionRelations = relations(companyDescription, ({one}) => ({
	company: one(company, {
		fields: [companyDescription.companyId],
		references: [company.id]
	}),
}));

export const occupationNameRelations = relations(occupationName, ({one}) => ({
	occupation: one(occupation, {
		fields: [occupationName.occupationId],
		references: [occupation.id]
	}),
}));

export const seniorityNameRelations = relations(seniorityName, ({one}) => ({
	seniority: one(seniority, {
		fields: [seniorityName.seniorityId],
		references: [seniority.id]
	}),
}));

export const occupationDomainNameRelations = relations(occupationDomainName, ({one}) => ({
	occupationDomain: one(occupationDomain, {
		fields: [occupationDomainName.domainId],
		references: [occupationDomain.id]
	}),
}));

export const locationNameRelations = relations(locationName, ({one}) => ({
	location: one(location, {
		fields: [locationName.locationId],
		references: [location.id]
	}),
}));

export const industryNameRelations = relations(industryName, ({one}) => ({
	industry: one(industry, {
		fields: [industryName.industryId],
		references: [industry.id]
	}),
}));