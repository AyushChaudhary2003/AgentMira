"""
Domain vocabularies and knowledge that the heuristic extractor and matcher
rely on. Kept in one place so it's easy to audit and extend.

This is deliberately Miami-specific. In production this would be data-driven
(pulled from the MLS taxonomy and a geo service), but hard-coding it keeps the
case study self-contained and makes the agent's assumptions explicit.
"""

# Canonical neighborhoods present in the dataset, with the aliases buyers use.
# Maps an alias (lowercased) -> canonical neighborhood name.
NEIGHBORHOOD_ALIASES = {
    "brickell": "Brickell",
    "downtown": "Downtown Miami",
    "downtown miami": "Downtown Miami",
    "coral gables": "Coral Gables",
    "the gables": "Coral Gables",
    "gables": "Coral Gables",
    "pinecrest": "Pinecrest",
    "aventura": "Aventura",
    "north miami": "North Miami",
    "coconut grove": "Coconut Grove",
    "the grove": "Coconut Grove",
    "key biscayne": "Key Biscayne",
    "bal harbour": "Bal Harbour",
    "bal harbor": "Bal Harbour",
    "wynwood": "Wynwood",
    "miami beach": "Miami Beach",
    "south beach": "South Beach",
    "mid-beach": "Mid-Beach",
    "mid beach": "Mid-Beach",
    "north beach": "North Beach",
    "edgewater": "Edgewater",
    "doral": "Doral",
}

# Soft adjacency: if a buyer wants X and we run short on inventory, listings in
# an adjacent neighborhood are a reasonable "you might also consider" rather than
# a hard match. Used only for partial location credit.
NEIGHBORHOOD_ADJACENCY = {
    "Brickell": {"Downtown Miami", "Edgewater", "Coconut Grove"},
    "Downtown Miami": {"Brickell", "Edgewater", "Wynwood"},
    "Edgewater": {"Downtown Miami", "Wynwood", "Brickell"},
    "Wynwood": {"Edgewater", "Downtown Miami"},
    "Coconut Grove": {"Coral Gables", "Brickell"},
    "Coral Gables": {"Coconut Grove", "Pinecrest", "South Miami"},
    "Pinecrest": {"Coral Gables"},
    "Aventura": {"Bal Harbour", "North Miami"},
    "Bal Harbour": {"Aventura", "Miami Beach", "North Beach"},
    "North Miami": {"Aventura"},
    "Key Biscayne": set(),
    "Miami Beach": {"South Beach", "Mid-Beach", "North Beach", "Bal Harbour"},
    "South Beach": {"Miami Beach", "Mid-Beach"},
    "Mid-Beach": {"Miami Beach", "South Beach", "North Beach"},
    "North Beach": {"Miami Beach", "Mid-Beach", "Bal Harbour"},
}

# property type keywords -> canonical type in dataset
PROPERTY_TYPE_KEYWORDS = {
    "condo": "Condo",
    "apartment": "Condo",
    "townhouse": "Townhouse",
    "townhome": "Townhouse",
    "single family": "Single Family",
    "single-family": "Single Family",
    "house": "Single Family",
    "home": None,  # too generic to pin a type; let other signals decide
    "villa": "Villa",
    "multi-family": "Multi-Family",
    "multifamily": "Multi-Family",
    "duplex": "Multi-Family",
    "multi family": "Multi-Family",
}

# Map buyer phrasing -> canonical MLS feature tag. Several buyer phrases collapse
# onto one tag (e.g. "parking"/"garage" -> Garage).
FEATURE_SYNONYMS = {
    "pool": "Pool",
    "gym": "Gym",
    "fitness": "Gym",
    "balcony": "Balcony",
    "terrace": "Terrace",
    "rooftop": "Rooftop",
    "garage": "Garage",
    "parking": "Garage",
    "ocean view": "Ocean View",
    "ocean-view": "Ocean View",
    "sea view": "Ocean View",
    "bay view": "Bay View",
    "waterfront": "Waterfront",
    "water front": "Waterfront",
    "boat dock": "Boat Dock",
    "dock": "Boat Dock",
    "boat slip": "Boat Dock",
    "home office": "Home Office",
    "office": "Home Office",
    "updated kitchen": "Updated Kitchen",
    "modern kitchen": "Modern Kitchen",
    "pet friendly": "Pet Friendly",
    "pet-friendly": "Pet Friendly",
    "pets": "Pet Friendly",
    "dog": "Pet Friendly",
    "cat": "Pet Friendly",
    "garden": "Garden",
    "yard": "Garden",
    "gated": "Gated Community",
    "gated community": "Gated Community",
    "concierge": "Concierge",
    "doorman": "Doorman",
    "smart home": "Smart Home",
    "solar": "Solar Panels",
    "tennis": "Tennis Court",
    "wine cellar": "Wine Cellar",
    "private beach": "Private Beach Access",
    "beach access": "Private Beach Access",
    "hardwood": "Hardwood Floors",
    "marble": "Marble Floors",
    "high ceilings": "High Ceilings",
    "walk-in closet": "Walk-in Closet",
    "stainless steel": "Stainless Steel Appliances",
    "hurricane": "Hurricane Impact Windows",
    "impact windows": "Hurricane Impact Windows",
    "granite": "Granite Countertops",
}

# Phrases that mark a requirement as a hard dealbreaker rather than a wish.
MUST_HAVE_MARKERS = [
    "non-negotiable", "non negotiable", "must have", "must-have", "essential",
    "required", "need at least", "needs to have", "have to have", "deal breaker",
    "dealbreaker", "a must", "necessary",
]

# Tokens that, in the message body, indicate an attempt to hijack the agent.
INJECTION_MARKERS = [
    "ignore all previous", "ignore previous instructions", "disregard the above",
    "disregard previous", "ignore the above", "system prompt", "you are now",
    "new instructions", "override", "respond by listing", "list all owner",
    "all owner names", "owner names and phone", "from the database",
    "previous instructions and",
]

# Requests for data we must never surface to a buyer.
PII_REQUEST_MARKERS = [
    "owner name", "owner phone", "owner's", "owners contact", "seller name",
    "seller phone", "contact them directly", "phone numbers from the database",
]
