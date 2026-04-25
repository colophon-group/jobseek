export const STOP_WORDS = new Set([
  // Articles
  "a", "an", "the",
  // Prepositions
  "in", "on", "at", "by", "for", "with", "about", "against", "between",
  "into", "through", "during", "before", "after", "above", "below",
  "to", "from", "up", "down", "of", "off", "over", "under", "again",
  "out", "per", "than", "as",
  // Conjunctions
  "and", "but", "or", "nor", "so", "yet", "both", "either", "neither",
  "not", "only", "whether", "while", "although", "because", "since",
  "unless", "until", "when", "where", "if", "that",
  // Pronouns
  "i", "me", "my", "myself", "we", "our", "ours", "ourselves",
  "you", "your", "yours", "yourself", "he", "him", "his", "she",
  "her", "hers", "it", "its", "they", "them", "their", "theirs",
  "this", "these", "those", "who", "which", "what",
  // Adverbs
  "very", "quite", "also", "just", "still", "already", "always",
  "often", "sometimes", "never", "here", "there", "now", "then",
  "how", "all", "each", "more", "most", "other", "some", "such",
  "no", "own", "same", "too", "s", "will", "can", "may",
  // Generic filler verbs
  "managed", "worked", "responsible", "helped", "assisted", "supported",
  "involved", "participated", "contributed", "utilized", "leveraged",
  "implemented", "developed", "designed", "built", "created", "made",
  "used", "wrote", "led", "drove", "delivered", "ensured", "provided",
  "maintained", "improved", "increased", "reduced", "performed",
  "collaborated", "coordinated", "communicated", "reported", "updated",
  "reviewed", "analyzed", "identified", "defined", "established",
  "executed", "operated", "monitored", "tested", "deployed", "migrated",
  "integrated", "configured", "handled", "processed",
  "achieved", "completed", "demonstrated", "applied", "followed",
  "including", "etc",
]);
