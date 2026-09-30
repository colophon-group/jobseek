import { msg } from "@lingui/core/macro";

/** Fictional descriptions that make both sides of the preview criteria explicit. */
export const narrowedPreviewExamples = [
  {
    id: "product-engineer",
    matches: true,
    title: msg({ id: "pro.example.productTitle", comment: "Illustrative job title in the Narrowed preview", message: "Product Engineer" }),
    excerpt: msg({ id: "pro.example.productExcerpt", comment: "Matching example: direct user contact, product improvements, no management", message: "Talk with users weekly and turn their feedback into product improvements. An individual contributor role with no people management." }),
  },
  {
    id: "solutions-engineer",
    matches: true,
    title: msg({ id: "pro.example.solutionsTitle", comment: "Illustrative job title in the Narrowed preview", message: "Solutions Engineer" }),
    excerpt: msg({ id: "pro.example.solutionsExcerpt", comment: "Matching example: direct customer contact, product improvements, no direct reports", message: "Work directly with customers to understand their workflows and build improvements with our product team. No direct reports." }),
  },
  {
    id: "engineering-manager",
    matches: false,
    title: msg({ id: "pro.example.managerTitle", comment: "Illustrative job title in the Narrowed preview", message: "Engineering Manager" }),
    excerpt: msg({ id: "pro.example.managerExcerpt", comment: "Excluded example: user contact but people management conflicts with criteria", message: "Turn user feedback into product improvements while leading eight engineers. Own hiring, performance reviews, and career development." }),
  },
  {
    id: "infrastructure-engineer",
    matches: false,
    title: msg({ id: "pro.example.infrastructureTitle", comment: "Illustrative job title in the Narrowed preview", message: "Infrastructure Engineer" }),
    excerpt: msg({ id: "pro.example.infrastructureExcerpt", comment: "Excluded example: individual contributor without the required user contact", message: "Maintain internal deployment systems and resolve reliability incidents as an individual contributor. No direct user or customer contact." }),
  },
];
