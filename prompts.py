summarize_notes_prompt_template = """
    Summarize the Notes below into several bullet points in short and precise style.
    - Resolve contradictions by preferring later statements.
    - Deduplicate and merge overlaps.
    - Preserve concrete facts (numbers, dates, roles, requirements, constraints, locations, compensation, visa, interview steps).
    - Ignore administrative/meta text (e.g., “title:”, “priority:”, separators) and speaker names unless essential.
    - Use a neutral, short and concise style.
    
    Output only the bullet list—no heading, preface, or closing text.
    
    Notes:
    {notes}

"""


extract_jd_experience_requirement = """
    Task
    Extract the required years of experience from the Provided Job Info. 
    Give min/max year extracted from the Provided Job Info, and suggested min/max year if there is no min/max year exact value.
    Give reason for both.

    Rules
    1) Return integers for min_year and max_year. If not found, use null.
    2) If a range is given (e.g., “3–5 years” or “3 to 5 years”): min_year=3, max_year=5.
    3）If more than some year, for example "more than 3 years", take it as "larger and equal" rather just "more than". For "Less than", take it as "less and equal".
    3) If only a maximum is given (e.g., “up to 5 years”): min_year=null, max_year=5.
    4) If min_year or max_year is null, Based on Job title, seniority, location to suggest suggest_min_year/suggest_max_year. If min_year/max_year is not null, use min_year/max_year as suggest_min_year/suggest_max_year. 
    5) suggest_min_year/suggest_max_year must not be null.
    6) Ignore numbers unrelated to required experience (e.g., headcount, salary, “years old”).
    7) If there are conflict min_year, max_year info. Take the value at "form_value". If not available in "form_value", take the value at "notes" if available.
    8) Do not output "None" or "null" in the output.
    9) Output "minmax_year_extract_reason" for "min_year" and "max_year", and output "minmax_year_suggest_reason" for "suggest_min_year" and "suggest_max_year".

    Output (Markdown code block with JSON only; no extra text)
    ```json
    {{
      "minmax_year_extract_reason": "<1–2 concise sentences explaining how min/max year were determined>",
      "min_year": <integer or null>,
      "max_year": <integer or null>,
      "minmax_year_suggest_reason": "<1–2 concise sentences explaining how suggested min/max year were determined>",
      "suggest_min_year": <integer>,
      "suggest_max_year": <integer>
    }}
    ---

    Input Job Info: 
    {job_desc}

"""


jd_extraction_prompt_template = """
    Extract key details from the provided job info and output only a markdown ````json``` block with these fields:
    - job_summary: A brief summary that preserves the most important details (key skills, education, current role, and prior roles).
    - job_title: The two most likely concise job titles inferred from the description (not necessarily the exact title shown).
                 Use short titles (e.g., "Senior Backend Engineer" instead of "Senior Backend Software Engineer").
                 LANGUAGE REQUIREMENT (MANDATORY): job_title values MUST be in English regardless of the input JD's language. If the JD is in Chinese (or any other non-English language), translate to the canonical English industry title used on LinkedIn / in international job postings. Examples: "后端工程师" → "Backend Engineer"; "高级算法工程师" → "Senior Algorithm Engineer"; "产品经理" → "Product Manager"; "数据分析师" → "Data Analyst"; "机器学习工程师" → "Machine Learning Engineer"; "前端开发工程师" → "Frontend Engineer". Never output Chinese characters, pinyin, or any non-English script; do not mix languages inside a title.
    - prefer_candidate_company: Companies the employer prefers candidates to have worked at (if mentioned). This is not the hiring company.
    - hiring_company: The name of the company that is hiring for this position. Look for "Hiring Company:" in the form input or company name mentioned as the employer.

    Omit any field that has no value (do not output None or null).
    
    ---
    
    Provided Job Info:
    {job_description}

"""

jd_extraction_language_template = """
    Extract required spoken language(s) from the provided job info and return only a markdown json block:
    {{
      "job_required_language": ["..."],
      "reason": "..."
    }}
    
    Rules:
    1. job_required_language must be a list of official language names chosen only from the Provided Applicable Language List below.
        - If the job info EXPLICITLY lists language requirements (e.g. "Required Languages: English", "fluent Japanese required", "必须会日语"), return exactly those languages. An explicit mention always wins over any default.
        - If a synonym appears, map it to the official name in the list (e.g., "Mandarin" → "Chinese").
        - If no language is explicitly specified, decide based on the JOB LOCATION's country, NOT on the language the JD is written in. The working language the employee will speak on the job is determined by where the role is based.
            - Location in an English-primary country (e.g. United States, United Kingdom, Canada (excluding Quebec), Australia, New Zealand, Ireland, and other countries where English is the sole or dominant official working language) → return ["English"].
            - Location in a non-English-primary country (e.g. Japan, China, Taiwan, Hong Kong, South Korea, Germany, France, Spain, Italy, Netherlands, Sweden, Poland, Brazil, Mexico, Russia, Turkey, Saudi Arabia, UAE, Thailand, Vietnam, Indonesia, Malaysia, etc.) → return the country's primary working language (e.g. Japan → ["Japanese"]; Tokyo, Japan → ["Japanese"]; Berlin, Germany → ["German"]; Shanghai, China → ["Chinese"]; Paris, France → ["French"]).
            - Do NOT add English to the list just because the JD is written in English. Writing a posting in English is a publishing convention — it does NOT imply English is the on-the-job working language.
            - Only add English in the non-English-primary-country case if the JD clearly says English is required (see the explicit-mention rule above).
            - If the location is missing/unclear and no language is explicit, fall back to the language the JD itself is written in.

    2. reason: 1–3 concise sentences explaining why the returned language(s) are required. Cite the location-derived inference or the explicit mention you used.
    3. Never output null, None, or any keys beyond the two above.
    
    Provided Applicable Language List:
    ['Abkhazian', 'Afar', 'Afrikaans', 'Akan', 'Albanian', 'Amharic', 'Arabic', 'Aragonese', 'Armenian', 'Assamese', 'Interlingua (International Auxiliary Language Association)', 'Interlingua (International Auxiliary Language Association)', 'Avaric', 'Avestan', 'Aymara', 'Azerbaijani', 'Bambara', 'Bashkir', 'Basque', 'Belarusian', 'Bengali', 'Bihari languages', 'Bislama', 'Bokmål, Norwegian', 'Bosnian', 'Breton', 'Bulgarian', 'Burmese', 'Catalan', 'Central Khmer', 'Chamorro', 'Chechen', 'Chinese', 'Church Slavonic', 'Chuvash', 'Cornish', 'Corsican', 'Cree', 'Croatian', 'Haitian Creole', 'Czech', 'Danish', 'Dhivehi', 'Dutch', 'Dzongkha', 'English', 'Esperanto', 'Estonian', 'Ewe', 'Faroese', 'Fijian', 'Finnish', 'French', 'Western Frisian', 'Fulah', 'Gaelic', 'Galician', 'Ganda', 'Georgian', 'German', 'Greek', 'Greenlandic', 'Guarani', 'Gujarati', 'Haitian Creole', 'Hausa', 'Hebrew', 'Herero', 'Hindi', 'Hiri Motu', 'Hungarian', 'Icelandic', 'Ido', 'Igbo', 'Indonesian', 'Interlingua (International Auxiliary Language Association)', 'Inuktitut', 'Inupiaq', 'Irish', 'Tonga (Tonga Islands)', 'Italian', 'Japanese', 'Javanese', 'Kannada', 'Kanuri', 'Kashmiri', 'Kazakh', 'Luba-Katanga', 'Central Khmer', 'Kikuyu', 'Kinyarwanda', 'Komi', 'Kongo', 'Korean', 'Kurdish', 'Kwanyama', 'Kyrgyz', 'Bihari languages', 'Interlingua (International Auxiliary Language Association)', 'Lao', 'Latin', 'Latvian', 'Limburgish', 'Lingala', 'Lithuanian', 'Luba-Katanga', 'Luxembourgish', 'Macedonian', 'Malagasy', 'Malay', 'Malayalam', 'Maltese', 'Manx', 'Maori', 'Marathi', 'Marshallese', 'Hiri Motu', 'Mongolian', 'Nauru', 'Navajo', 'Ndebele, North', 'Ndebele, South', 'Ndonga', 'Nepali', 'Bokmål, Norwegian', 'Ndebele, North', 'Northern Sami', 'Norwegian', 'Nynorsk, Norwegian', 'Nuosu', 'Nyanja', 'Nynorsk, Norwegian', 'Occidental', 'Occitan', 'Ojibwa', 'Oriya', 'Oromo', 'Ossetic', 'Pali', 'Pashto', 'Persian', 'Polish', 'Portuguese', 'Punjabi', 'Quechua', 'Romanian', 'Romansh', 'Rundi', 'Russian', 'Northern Sami', 'Samoan', 'Sango', 'Sanskrit', 'Sardinian', 'Serbian', 'Shona', 'Sindhi', 'Sinhalese', 'Church Slavonic', 'Slovak', 'Slovenian', 'Ndebele, South', 'Somali', 'Sotho, Southern', 'Spanish', 'Sundanese', 'Swahili', 'Swati', 'Swedish', 'Tagalog', 'Tahitian', 'Tajik', 'Tamil', 'Tatar', 'Telugu', 'Thai', 'Tibetan', 'Tigrinya', 'Tonga (Tonga Islands)', 'Tsonga', 'Tswana', 'Turkish', 'Turkmen', 'Twi', 'Ukrainian', 'Urdu', 'Uyghur', 'Uzbek', 'Venda', 'Vietnamese', 'Volapük', 'Walloon', 'Welsh', 'Western Frisian', 'Wolof', 'Xhosa', 'Yiddish', 'Yoruba', 'Zhuang', 'Zulu']

    ---

    Provided Job Info:
    {job_desc}

"""



extract_job_title_prompt = """
    Task:
    Find most likely job titles based on Provided Job Info. Provide top 3 job titles with probability.
    Ordering from high to low likelihood with probability. The probability must be normalized to 1.

    Guidelines:
    - Use responsibilities, tools, and outcomes in the description to infer the job title.
    - If ambiguous, pick the best-supported label and note the uncertainty in the reason.
    - Do not output "None" or "null" in the title output.
    - LANGUAGE REQUIREMENT (MANDATORY): Every job title in the output MUST be in English, regardless of the input JD's language. If the JD is in Chinese (or any other non-English language), translate to the canonical English industry title used on LinkedIn / in international job postings. Examples: "后端工程师" → "Backend Engineer"; "高级算法工程师" → "Senior Algorithm Engineer"; "产品经理" → "Product Manager"; "数据分析师" → "Data Analyst"; "机器学习工程师" → "Machine Learning Engineer". Never emit Chinese characters, pinyin, or any non-English script; do not mix languages inside a single title.

    ---

    Output (Markdown code block with JSON only; no extra text):
    ```json
    {{
      "reason": "<1–2 concise sentences explaining why>",
      "title": [[<job_title_1>, probability], [<job_title_2>, probability], ...]
    }}

    ---

    Provided Job Info:
    {job_desc}
"""


extract_job_location_prompt = """
    Task
    Extract the required job location(s) from the input Job Info.
    
    Normalization rules
    1) Return every required location mentioned. If none, return [].
    2) Output each location as a full-name string in: "city region state country" when applicable.
     - Example: "San Mateo, CA" → "San Mateo California United States"
    3) If it's municipality city, output as "city, country"
     - Example: "Beijing" -> "Beijing, China"
    4) Worldwide metro / greater-area expansion (CRITICAL — do this for ANY city, anywhere in the world, using your own geographic knowledge):
       - If the city is part of a recognized metropolitan area, greater area, or metropolitan region, expand to the canonical English name of that metro. Do NOT keep the city alone, and do NOT use sub-national region/state/province names alone.
       - Use ONE consistent canonical name per metro across runs. Never produce two variants for the same metro (e.g., "Paris Île-de-France France" and "Greater Paris Metropolitan Region" must never both appear for the same input).
       - Canonical name patterns (pick the one that LinkedIn uses for the metro — these are illustrative, not an exhaustive lookup list):
           * "Greater <City> Metropolitan Region"   e.g., "Greater Paris Metropolitan Region"
           * "Greater <City> Area"                  e.g., "Greater Boston", "Greater Tokyo Area", "Greater Seattle Area"
           * "<City> Metropolitan Area"             e.g., "Los Angeles Metropolitan Area", "Detroit Metropolitan Area", "Madrid Metropolitan Area"
           * "<City> Bay Area"                      e.g., "San Francisco Bay Area"
           * "Greater <City>"                       e.g., "Greater London", "Greater Berlin", "Greater Munich"

       HARD RULES (the failure modes to avoid):
       - NEVER expand a city to the country alone when a metro exists. "Paris, France" must NOT become "France". The country alone is too broad.
       - NEVER use a sub-national region/state/province name alone (e.g., "Île-de-France", "Bavaria", "California", "Greater London Region with the bare region name"). Use the metro name.
       - NEVER output the city + region + country form (e.g., "Paris Île-de-France France") when a recognized metro exists — output the metro name instead.
       - For municipal city-states or cities with no recognized larger metro (Singapore, Monaco, Hong Kong, Macau), keep the city / SAR name.
       - For mainland Chinese cities, Chinese tier-1 cities are typically named at city level by LinkedIn (e.g., "Beijing, China", "Shanghai, China", "Shenzhen, China") rather than as "Greater <City>" — follow that convention.
       - Prefer the English canonical name even when a native-language name exists (e.g., "Greater Munich" not "Großraum München").
       - When genuinely uncertain about the canonical metro name, prefer the "Greater <City> Area" pattern over inventing a regional name.

       Worldwide examples (these are illustrative — apply the same logic to ANY city using your knowledge):
       - Paris, France                → "Greater Paris Metropolitan Region"
       - London, UK                   → "Greater London"
       - Tokyo / Yokohama, Japan      → "Greater Tokyo Area"
       - Sydney, Australia            → "Greater Sydney Area"
       - Berlin, Germany              → "Greater Berlin"
       - Munich, Germany              → "Greater Munich"
       - Madrid, Spain                → "Madrid Metropolitan Area"
       - Barcelona, Spain             → "Barcelona Metropolitan Area"
       - Toronto, Canada              → "Greater Toronto Area"
       - Vancouver, Canada            → "Greater Vancouver Metropolitan Area"
       - Mexico City, Mexico          → "Mexico City Metropolitan Area"
       - São Paulo, Brazil            → "Greater São Paulo"
       - Bengaluru / Bangalore, India → "Bengaluru Metropolitan Area"
       - Mumbai, India                → "Mumbai Metropolitan Region"
       - Seoul, South Korea           → "Greater Seoul"
       - Singapore                    → "Singapore"  (city-state, no metro expansion)
       - Hong Kong                    → "Hong Kong SAR"
       - Beijing, China               → "Beijing, China"  (LinkedIn uses city-level)
       - Shanghai, China              → "Shanghai, China"  (LinkedIn uses city-level)
       - San Mateo / Mountain View    → "San Francisco Bay Area"
       - Cambridge / Somerville MA    → "Greater Boston"
       - Bellevue / Redmond WA        → "Greater Seattle Area"
       - Anaheim / Santa Monica CA    → "Los Angeles Metropolitan Area"
       - Dearborn / Ann Arbor MI      → "Detroit Metropolitan Area"
       - Newark NJ / Jersey City      → "New York City Metropolitan Area"

    5) Normalize informal area names to the canonical metro form by following the same patterns above:
       - "SF Bay Area" / "San Francisco Bay Area CA" → "San Francisco Bay Area"
       - "Seattle Area WA"                          → "Greater Seattle Area"
       - "New York City"                            → "New York City Metropolitan Area"
       - "Paris"  /  "Paris Île-de-France"          → "Greater Paris Metropolitan Region"
       - "London UK"                                → "Greater London"
       - "Tokyo"                                    → "Greater Tokyo Area"
     
    6) Remote handling:
     - If JD mentions "remote", "work from home", "WFH", or similar remote work options:
       a) If other physical city/area locations are also mentioned, replace "Remote" with the country name of those locations (e.g., "United States", "China").
          Do NOT output "Remote" as a separate entry — just use the country name.
       b) If NO physical city/area locations are mentioned and a hiring company is provided, infer the country where the hiring company is located and use that country name.
       c) Only output "Remote" if you cannot determine any country from the above steps.
     
    7) Never output null/None. Return only JSON.
    
    Output (JSON only; in a Markdown code block)
    {{
    "reason": "<1–2 concise sentences citing the phrases used>",
    "job_location": ["<loc1>", "<loc2>", "..."]
    }}
    
    
    ---
    Few-shot examples
    
    Example 1
    JD: "This role is based in San Mateo, CA."
    Output:
    {{
    "reason": "The JD states the role is based in San Mateo, CA. San Mateo is in San Mateo County, part of the San Francisco Bay Area per rule 4.",
    "job_location": ["San Francisco Bay Area"]
    }}
    
    Example 2
    JD: "Hybrid in Mountain View, CA (3 days onsite)."
    Output:
    {{
    "reason": "The JD requires onsite work in Mountain View, CA; expand to the Bay Area per rules.",
    "job_location": ["San Francisco Bay Area"]
    }}
    
    Example 3
    JD: "Location: SF Bay Area or Seattle Area, WA."
    Output:
    {{
    "reason": "The JD lists SF Bay Area and Seattle Area, WA; both are normalized to regulated area names.",
    "job_location": ["San Francisco Bay Area", "Greater Seattle Area"]
    }}
    
    Example 4
    JD: "Remote in US with occasional travel to NYC."
    Output:
    {{
    "reason": "The JD specifies remote work in the US. Per rule 6a, replace Remote with the country name.",
    "job_location": ["United States"]
    }}
    
    Example 5
    JD: "Preferred: candidates near New York City. No location requirement."
    Output:
    {{
    "reason": "No required job location is stated; 'preferred' is not a requirement.",
    "job_location": []
    }}
    
    Example 6
    JD: "This is a remote position. Hiring Company: Tetramem"
    Output:
    {{
    "reason": "The JD is remote with no city locations. Hiring company Tetramem is based in the US. Per rule 6b, use country name.",
    "job_location": ["United States"]
    }}
    
    Example 7
    JD: "Remote-friendly with offices in Chicago and Boston."
    Output:
    {{
    "reason": "The JD offers remote work with offices in Chicago and Boston. Per rule 6a, replace Remote with country name.",
    "job_location": ["Greater Chicago Area", "Greater Boston", "United States"]
    }}
    
    Example 8
    JD: "Work from home available. Based in Los Angeles, CA."
    Output:
    {{
    "reason": "The JD is based in LA with WFH option. Per rule 6a, replace Remote with country name.",
    "job_location": ["Los Angeles Metropolitan Area", "United States"]
    }}

    Example 9
    JD: "Office located in Fremont, CA."
    Output:
    {{
    "reason": "Fremont is in the San Francisco Bay Area; expanding to metro area per rule 3.",
    "job_location": ["San Francisco Bay Area"]
    }}

    Example 10
    JD: "This role is based in Paris, France."
    Output:
    {{
    "reason": "Paris is part of the Greater Paris Metropolitan Region; expanded to the canonical metro name per rule 4. Country-only ('France') and region-only ('Île-de-France') outputs are forbidden.",
    "job_location": ["Greater Paris Metropolitan Region"]
    }}

    Example 11
    JD: "Hybrid role in London with occasional travel."
    Output:
    {{
    "reason": "London is in the Greater London metro per rule 4. Country-only output ('United Kingdom') is forbidden when a recognized metro exists.",
    "job_location": ["Greater London"]
    }}

    Example 12
    JD: "Location: Tokyo, Japan or Singapore."
    Output:
    {{
    "reason": "Tokyo expands to Greater Tokyo Area per rule 4. Singapore is a city-state and is kept as 'Singapore' per rule 4 exception.",
    "job_location": ["Greater Tokyo Area", "Singapore"]
    }}

    Example 13
    JD: "Based in Munich, Germany. Some travel to Berlin office."
    Output:
    {{
    "reason": "Munich → Greater Munich; Berlin → Greater Berlin per rule 4 worldwide metro expansion.",
    "job_location": ["Greater Munich", "Greater Berlin"]
    }}

    ---
    Input Job Info:
    {job_desc}

"""


extract_company_industry_prompt = """
    Task:
    From the Provided Job Info, identify the company’s most-likely industry. 
    Ordering from high to low likelihood with probability. The probability must be normalized to 1. 
    With "industry" and "prob" as sub key.
    Do not output "None" or "null" in the industry output.
    
    ---

    Allowed labels
    - Choose exactly one industry from:
    ['Abrasives and Nonmetallic Minerals Manufacturing', 'Accounting', 'Accessible Architecture and Design', 'Accessible Hardware Manufacturing', 'Digital Accessibility Services', 'Accommodation and Food Services', 'Claims Adjusting, Actuarial Services', 'Fashion Accessories Manufacturing', 'Education Administration Programs', 'Government Administration', 'Advertising Services', 'Administration of Justice', 'Utilities Administration', 'Office Administration', 'Administrative and Support Services', 'Investment Advice', 'Paint, Coating, and Adhesive Manufacturing', 'Aviation and Aerospace Component Manufacturing', 'International Affairs', 'Military and International Affairs', 'Collection Agencies', 'Insurance Agencies and Brokerages', 'Agriculture, Construction, Mining Machinery Manufacturing', 'Agricultural Chemical Manufacturing', 'Airlines and Aviation', 'Air, Water, and Waste Program Management', 'Water, Waste, Steam, and Air Conditioning Services', 'Alternative Dispute Resolution', 'Alternative Fuel Vehicle Manufacturing', 'Wholesale Alcoholic Beverages', 'Wholesale Chemical and Allied Products', 'Amusement Parks and Arcades', 'IT Services and IT Consulting', 'Hospitals and Health Care', 'Oil and Gas', 'Wellness and Fitness Services', 'Food and Beverage Services', 'Appliances, Electrical, and Electronics Manufacturing', 'Business Consulting and Services', 'Primary and Secondary Education', 'Transportation, Logistics, Supply Chain and Storage', 'Retail Apparel and Fashion', 'Retail Appliances, Electrical, and Electronic Equipment', 'Wholesale Apparel and Sewing Supplies', 'Wholesale Appliances, Electrical, and Electronics', 'Household Appliance Manufacturing', 'Apparel Manufacturing', 'Architecture and Planning', 'Travel Arrangements', 'Armed Forces', 'Retail Art Supplies', 'Artists and Writers', 'Performing Arts', 'Retail Art Dealers', 'Performing Arts and Spectator Sports', 'Public Assistance Programs', 'Automation Machinery Manufacturing', 'Audio and Video Equipment Manufacturing', 'Banking', 'Investment Banking', 'Bars, Taverns, and Nightclubs', 'Metal Valve, Ball, and Roller Manufacturing', 'Baked Goods Manufacturing', 'Food and Beverage Manufacturing', 'Beverage Manufacturing', 'Bed-and-Breakfasts, Hostels, Homestays', 'Insurance and Employee Benefit Funds', 'Food and Beverage Retail', 'Wholesale Food and Beverage', 'Biotechnology Research', 'Mattress and Blinds Manufacturing', 'Book and Periodical Publishing', 'Zoos and Botanical Gardens', 'Retail Books and Printed News', 'Boilers, Tanks, and Shipping Container Manufacturing', 'Broadcast Media Production and Distribution', 'Loan Brokers', 'Wholesale Building Materials', 'Retail Building Materials and Garden Equipment', 'Building Construction', 'Personal Care Product Manufacturing', 'Capital Markets', 'Gambling Facilities and Casinos', 'Venture Capital and Private Equity Principals', 'Death Care Services', 'Caterers', 'Nursing Homes and Residential Care Facilities', 'Telephone Call Centers', 'Insurance Carriers', 'Glass, Ceramics and Concrete Manufacturing', 'Fuel Cell Manufacturing', 'Chemical Manufacturing', 'Chemical Raw Materials Manufacturing', 'Civil Engineering', 'Civic and Social Organizations', 'Circuses and Magic Shows', 'Climate Data and Analytics', 'Climate Technology Product Manufacturing', 'Golf Courses and Country Clubs', 'Sports Teams and Clubs', 'Clay and Refractory Products Manufacturing', 'Soap and Cleaning Product Manufacturing', 'Construction', 'Consumer Services', 'Professional Training and Coaching', 'Computers and Electronics Manufacturing', 'Outsourcing and Offshoring Consulting', 'Public Relations and Communications Services', 'Computer Hardware Manufacturing', 'Credit Intermediation', 'IT System Custom Software Development', 'Cutlery and Handtool Manufacturing', 'Dairy Product Manufacturing', 'IT System Data Services', 'Data Infrastructure and Analytics', 'Dance Companies', 'Software Development', 'Design Services', 'Defense and Space Manufacturing', 'International Trade and Development', 'Graphic Design', 'Regenerative Design', 'Interior Design', 'IT System Installation and Disposal', 'Natural Gas Distribution', 'Electric Power Transmission, Control, and Distribution', 'Wholesale Drugs and Sundries', 'Economic Programs', 'Higher Education', 'Writing and Editing', 'Education', 'Electrical Equipment Manufacturing', 'Electric Lighting Equipment Manufacturing', 'Electric Power Generation', 'Entertainment Providers', 'Environmental Services', 'Law Enforcement', 'Energy Technology', 'Robotics Engineering', 'Services for Renewable Energy', 'Engineering Services', 'Engines and Power Transmission Equipment Manufacturing', 'Environmental Quality Programs', 'Medical Equipment Manufacturing', 'Retail Office Equipment', 'Railroad Equipment Manufacturing', 'Equipment Rental Services', 'Real Estate and Equipment Rental Services', 'Wholesale Hardware, Plumbing, Heating Equipment', 'Real Estate', 'Trusts and Estates', 'Events Services', 'IT System Testing and Evaluation', 'Wholesale Import and Export', 'Executive Offices', 'Securities and Commodity Exchanges', 'Facilities Services', 'Farming', 'Individual and Family Services', 'Recreational Facilities', 'Skiing Facilities', 'Wholesale Raw Farm Products', 'Turned Products and Fastener Manufacturing', 'Animal Feed Manufacturing', 'Financial Services', 'Fire Protection', 'Office Furniture and Fixtures Manufacturing', 'Artificial Rubber and Synthetic Fiber Manufacturing', 'Ranching and Fisheries', 'Retail Florists', 'Paper and Forest Product Manufacturing', 'Mobile Food Services', 'Wholesale Footwear', 'Freight and Package Transportation', 'Fruit and Vegetable Preserves Manufacturing', 'Furniture and Home Furnishings Manufacturing', 'Philanthropic Fundraising Services', 'Fundraising', 'Funeral Services', 'Pension Funds', 'Funds and Trusts', 'Retail Furniture and Home Furnishings', 'Computer Games', 'Retail Gasoline', 'Oil, Gas, and Mining', 'Retail Office Supplies and Gifts', 'Glass Product Manufacturing', 'Government Relations Services', 'Retail Luxury Goods and Jewelry', 'Sporting Goods Manufacturing', 'Wholesale Luxury Goods and Jewelry', 'Ground Passenger Transportation', 'Lime and Gypsum Products Manufacturing', 'Construction Hardware Manufacturing', "Women's Handbag Manufacturing", 'Public Health', 'Health and Human Services', 'Retail Health and Personal Care Products', 'Museums, Historical Sites, and Zoos', 'Historical Sites', 'Hospitality', 'Housing Programs', 'Housing and Community Development', 'Household Services', 'Hotels and Motels', 'Hospitals', 'Human Resources Services', 'HVAC and Refrigeration Equipment Manufacturing', 'Insurance', 'Industrial Machinery Manufacturing', 'Security and Investigations', 'Investment Management', 'Information Services', 'Religious Institutions', 'IT System Training and Support', 'IT System Operations and Maintenance', 'IT System Design Services', 'Law Practice', 'Courts of Law', 'Personal and Laundry Services', 'Legal Services', 'E-Learning Providers', 'Legislative Offices', 'Leather Product Manufacturing', 'Translation and Localization', 'Forestry and Logging', 'Motor Vehicle Manufacturing', 'Manufacturing', 'Pharmaceutical Manufacturing', 'Machinery Manufacturing', 'Medical Practices', 'Smart Meter Manufacturing', 'Technology, Information and Media', 'Retail Recyclable Materials & Used Merchandise', 'Wholesale Metals and Minerals', 'Magnetic and Optical Media Manufacturing', 'Measuring and Control Instrument Manufacturing', 'Metalworking Machinery Manufacturing', 'Mining', 'Movies, Videos, and Sound', 'Retail Motor Vehicles', 'Wholesale Motor Vehicles and Parts', 'Motor Vehicle Parts Manufacturing', 'Musicians', 'Museums', 'Retail Musical Instruments', 'Nanotechnology Research', 'Computer and Network Security', 'Social Networking Platforms', 'Non-profit Organizations', 'Public Policy Offices', 'Oil and Coal Product Manufacturing', 'Online and Mail Order Retail', 'Operations Consulting', 'Packaging and Containers Manufacturing', 'Wholesale Paper Products', 'Wholesale Petroleum and Petroleum Products', 'Photography', 'Retail Pharmacies', 'Wholesale Photography Equipment and Supplies', 'Pipeline Transportation', 'Plastics Manufacturing', 'Community Development and Urban Planning', 'Internet Marketplace Platforms', 'Plastics and Rubber Product Manufacturing', 'Postal Services', 'Printing Services', 'Public Safety', 'Racetracks', 'Rail Transportation', 'Farming, Ranching, Forestry', 'Retail', 'Restaurants', 'Research Services', 'Staffing and Recruiting', 'Robot Manufacturing', 'Rubber Products Manufacturing', 'Savings Institutions', 'Shipbuilding', 'Spectator Sports', 'Space Research and Technology', 'Spring and Wire Product Manufacturing', 'Specialty Trade Contractors', 'Warehousing and Storage', 'Strategic Management Services', 'Architectural and Structural Metal Manufacturing', 'Surveying and Mapping Services', 'Sugar and Confectionery Product Manufacturing', 'Think Tanks', 'Telecommunications', 'Textile Manufacturing', 'Technical and Vocational Training', 'Theater Companies', 'Tobacco Manufacturing', 'Truck Transportation', 'Maritime Transportation', 'Transportation Programs', 'Utilities', 'Veterinary Services', 'Wholesale', 'Wood Product Manufacturing']

    ---

    Output (JSON only; no extra text):
    ```json
    {{
      "reason": "<brief evidence from the description (products/services, domain terms, clientele, regulations, etc.)>"
      "industry": [[<industry_1>, probability], [<industry_2>, probability], ...],
    }}

    ---

    Provided Job Info: 
    {job_desc}

"""


extract_job_function_prompt = """
    Task:
    Find all the job function in Provided Job Info. 
    Ordering from high to low likelihood with probability. The probability must be normalized to 1. 
    
    Allowed labels (choose exactly one):
    ['Accounting', 'Administrative', 'Arts and Design', 'Business Development', 'Community and Social Services', 'Consulting', 'Education', 'Engineering', 'Entrepreneurship', 'Finance', 'Healthcare Services', 'Human Resources', 'Information Technology', 'Legal', 'Marketing', 'Media and Communication', 'Military and Protective Services', 'Operations', 'Product Management', 'Program and Project Management', 'Purchasing', 'Quality Assurance', 'Real Estate', 'Research', 'Sales', 'Customer Success and Support']
    
    Guidelines:
    - Use responsibilities, tools, and outcomes in the description to infer the function.
    - Treat synonyms/abbreviations case-insensitively (e.g., “PM” → product management or program/project management based on context).
    - If ambiguous, pick the best-supported label and note the uncertainty in the reason.
    - For Software position, if both management, cooperation and engineering requirement mentioned, or it's an engineer manager position, the major job function is "Engineering". 
    - Do not output "None" or "null" in the function output.
    
    ---
    
    Output (Markdown code block with JSON only; no extra text):
    ```json
    {{
      "reason": "<1–2 concise sentences explaining why>",
      "function": [[<job_function_1>, probability], [<job_function_2>, probability], ...]
    }}
    
    ---
    
    Provided Job Info:
    {job_desc}
"""


extract_key_responsibility_prompt = """
    Task
    Find all the job key responsibility in 1-3 keywords in Provided Job Info. 
    Ordering from high to low likelihood with probability. The probability must be normalized to 1. 

    Guidelines
    - Use responsibilities, tools, and outcomes in the description to infer the key responsibility.
    - Treat synonyms/abbreviations case-insensitively (e.g., “PM” → product management or program/project management based on context).
    - If ambiguous, pick the best-supported label and note the uncertainty in the reason.
    - Do not infer responsibility or department beyond the labels above.
    - Do not output "None" or "null" in the responsibility output.

    ---

    Output (Markdown code block with JSON only; no extra text)
    ```json
    {{
      "reason": "<1–2 concise sentences explaining why>",
      "key_responsibilities": [[<key_responsibility_1>, probability], [<key_responsibility_2>, probability], ...]
    }}

    ---

    Provided Job Info:
    {job_desc}

"""


extract_all_skills_prompt = """
    Based on Provided Job Info, find all the skills keywords in Provided Job Info, output with importance from high to low. 
        Even if it's basic skills like "Java", "Python", "Golang", "SQL", "NoSQL", "Node.JS", do output them.  
        "None" is not a skill, Do not output "None" or "null" in the output.
        Output with key "all_skills". 

    All skill keywords should be short and precise. Remove common basic skills from Each output list, ordering from high to low priority with probability. The probability must be normalized to 1. With "skill" and "probability" as sub key.
    Example input skill: "Backend Engineering (Node.JS, Scalable Systems)"
    Example output skill: "Backend Engineering"

    Example input skill: "Python (Flask)"
    Example output skill: "Flask"

    Example input skill: "API Development (RESTful)"
    Example output skill: "RESTful API"

    Example input skill: "Backend Engineering (Node.JS, Scalable Systems)"
    Example output skill: "Backend Engineering"

    Example input skill: "Scalable and Reliable Systems Development"
    Example output skill: "Scalable System"

    Example input skill: "Full Stack Development (5+ years)"
    Example output skill: "Full Stack"

    ---    

    Provided Job Info: 
    {job_desc}
"""


analyze_job_seniority_prompt = """
    Task:
    Find all the job seniority in Provided Job Info.
    Ordering from high to low likelihood with probability. The probability must be normalized to 1.

    Allowed labels:
    ['owner/partner', 'cxo', 'vice president', 'director', 'experienced manager', 'entry level manager', 'strategic', 'senior', 'entry level', 'in training']

    Guidelines:
    1. If the Provided Job Info explicitly names one of the labels (or an obvious synonym), return that label.
      - Exec keywords → 'cxo' (CEO/CTO/CFO/etc.), 'vice president' (VP), 'director', 'owner/partner'.
    2. If the Provided Job Info mentions this is a manager position, but no explicit seniority:
      - 0–3 years → 'entry level manager'
      - ≥4 years → 'experienced manager'
    3. If the Provided Job Info mention this is not a manager position. If no explicit seniority:
      - 0–1 years → 'in training'
      - 1–3 years → 'entry level'
      - 4–7 years → 'senior'
      - >7 years → 'strategic' (use 'experienced manager' instead if management duties are clear)

    Year Range Mapping for Seniority Levels:
    - 'owner/partner': min_year=15, max_year=99  # Very senior, typically 15+ years
    - 'cxo': min_year=15, max_year=99  # C-level executives, 15+ years
    - 'vice president': min_year=12, max_year=99  # VP level, typically 12+ years
    - 'director': min_year=8, max_year=20  # Director level, 8-20 years typical
    - 'experienced manager': min_year=4, max_year=15  # Experienced managers, 4-15 years
    - 'entry level manager': min_year=0, max_year=3  # New managers, 0-3 years
    - 'strategic': min_year=7, max_year=15  # Strategic IC roles, 7-15 years
    - 'senior': min_year=4, max_year=7  # Senior IC roles, 4-7 years
    - 'entry level': min_year=1, max_year=3  # Entry level roles, 1-3 years
    - 'in training': min_year=0, max_year=1  # Training/intern roles, 0-1 year
    
    
    ---

    Output (Markdown code block with JSON only; no extra text): 
    ```json
    {{
      "reason": "<1–2 concise sentences explaining why>",
      "seniority": [["seniority_1", probability], ["seniority_2", probability], ...],
      "year_of_experience": [
        ["seniority_name_1", probability, {{"start_num_year": x, "end_num_year": y}}],
        ["seniority_name_2", probability, {{"start_num_year": x, "end_num_year": y}}]
      ]
    }}
    ```
    Note: 
    

    Example Output:
    ```json
    {{
      "reason": "The job requires 5+ years experience and mentions leading projects but not people management, indicating a senior individual contributor role.",
      "seniority": [["senior", 0.7], ["strategic", 0.3]],
      "year_of_experience": [
        ["senior", 0.7, {{"start_num_year": 4, "end_num_year": 7}}],
        ["strategic", 0.3, {{"start_num_year": 7, "end_num_year": 15}}]
      ]
    }}
    ```

    IMPORTANT:
    - Use lists with square brackets [] for both "seniority" and "year_of_experience", not tuples with parentheses ()
    - "seniority" entries should be lists with 2 elements: ["seniority_name", probability]
    - "year_of_experience" entries should be lists with 3 elements: ["seniority_name", probability, {{"start_num_year": x, "end_num_year": y}}]
    - The seniority names in both "seniority" and "year_of_experience" must match exactly
    - The start_num_year and end_num_year values should correspond to the year range mapping provided above (use min_year as start_num_year, max_year as end_num_year)
    - "end_num_year" should be at least 1 year larger than "start_num_year"
    - Probabilities must sum to 1.0 and should be the same in both "seniority" and "year_of_experience"
    - Do not output "None" or "null" in the output.

    ---

    Provided Job Info:
    {job_desc}

"""


job_skills_understanding_prompt = """
    Based on Provided Job Info, finish the following tasks and response in json.

    1. Find the top 5 most important skill keywords, output with key "jd_skills".
    2. Give your suggested the top 5 most important skill keywords from high to low priority with probability.
        Only keep the most critical skills. Do Remove common basic skills for that industry.
        For example, "Java" or "Python" or "Golang" or "SQL" or "NoSQL" or "Node.JS" are a basic skill for Software Engineer, do not suggest them.        

    3. Based on above "jd_skills", "suggested_skills", find the top 4 mandatory must have skills, output with key "mandatory_skills".
    4. Based on above "jd_skills", "suggested_skills", find good to have skills, Output with key "good_to_have_skills".

    5. All skill keywords should be short and precise. Remove common basic skills from Each output list, ordering from high to low priority with probability. The probability must be normalized to 1. With "skill" and "probability" as sub key.
    Example input skill: "Backend Engineering (Node.JS, Scalable Systems)"
    Example output skill: "Backend Engineering"

    Example input skill: "Python (Flask)"
    Example output skill: "Flask"

    Example input skill: "API Development (RESTful)"
    Example output skill: "RESTful API"

    Example input skill: "Backend Engineering (Node.JS, Scalable Systems)"
    Example output skill: "Backend Engineering"

    Example input skill: "Scalable and Reliable Systems Development"
    Example output skill: "Scalable System"

    Example input skill: "Full Stack Development (5+ years)"
    Example output skill: "Full Stack"
    
    6. "None" or "null" is not a skill, do not output "None" or "null" in your output for any skill category.

    7. LANGUAGE REQUIREMENT (MANDATORY): Every skill name in every output list ("jd_skills", "suggested_skills", "mandatory_skills", "good_to_have_skills") MUST be written in English, regardless of the input JD's language. If the JD is in Chinese (or any other non-English language), translate each skill into its standard English industry term.
       - Examples: "后端开发" → "Backend Development"; "机器学习" → "Machine Learning"; "数据分析" → "Data Analysis"; "产品经理" → "Product Management"; "算法工程" → "Algorithm Engineering"; "分布式系统" → "Distributed Systems".
       - Prefer the canonical English term used on LinkedIn / in international job postings. Do NOT output Chinese characters, pinyin, or any non-English script. Do NOT mix languages inside a single skill name.


    Your output should be json with key "jd_skills", "suggested_skills", "mandatory_skills", "good_to_have_skills".

    ---

    Provided Job Info:
    {job_desc}
"""

job_skills_synonym_prompt = """
    Task: Given a job skill phrase, return several closely related skill names (synonyms, variants, or canonical forms).

    Examples
    Input: Python (Flask)
    Output: ["Flask", "Python"]

    Input: API Development (RESTful)
    Output: ["RESTful API", "Rest API Development"]

    Input: ML serving
    Output: ["Model Serving", "Machine Learning Serving"]

    Input: Scalable and Reliable Systems Development
    Output: ["Scalable Systems", "Scalable Systems Development"]

    Input: Full Stack Development
    Output: ["Full Stack", "Full Stack Development"]

    ---

    Provided job skill: {job_skill}

    --- 

    Instructions:
    - Output only a list of strings (e.g., ["Flask", "Python"]).
    - No explanations or extra text.
    - Do not output "None" or "null" in the output.
    - All synonyms MUST be in English. If the input skill is in Chinese or any other non-English language, translate to the canonical English industry term and return English variants only. Never emit Chinese characters, pinyin, or any non-English script.
"""


find_most_likely_entity_name_prompt = """
    Task: Choose the single best-matching candidate name for a given entity. If none match, return None.

    Inputs
    - entity_name: {entity_name}
    - candidates: {candidate_item_names}

    Rules
    - Match semantically (aliases, abbreviations, canonical forms).
    - Ignore case and diacritics.
    - Output exactly one candidate string from the list, or None. No extra text.

    Example
    Entity: "US"
    Candidates: ['United States', 'US Virgin Islands', 'Uzbekistan', 'Uster, Zurich, Switzerland', 'Ushuaia, Tierra del Fuego Province, Argentina', 'Ústí nad Labem, Czechia']
    Output: "United States" 

    Entity: "DigitalOcean"
    Candidates: []
    Output: None
"""


candidate_location_matching_prompt = """
    Task:
    Decide if the candidate’s location matches any job location.
    
    Rules:
    - Match if ANY of the following is true (case-insensitive):
      1) Exact same place (city/state/region/country).
      2) Same metro/region. Candidate is within a job location (e.g., "San Jose" ∈ "SF Bay Area"; "Cambridge" ∈ "MA, USA"; "San Diego" ∈ "USA").
      3) Job location is within the candidate location (e.g., candidate is in "USA" matches job "SF Bay Area").
    - If multiple job locations are given, return true if any one matches.
    - If unclear or not recognized, return false.
    - Job Location(s) is a list of one or more locations
    - Candidate Location is only one location, For example, if candidate location is "Twinsburg, Ohio, United States", that's one location (Twinsburg, Ohio within USA), not multiple locations
    - Do not output "None" or "null" in the location output.
    
    Input:
    Job Location(s): {job_location_list}
    Candidate Location: {candidate_location}
    
    Output (JSON only in a Markdown code block; no extra text):
    ```json
    {{
      "reason": "<1–2 concise sentences>",
      "location_matching": boolean
    }}

"""

preferred_company_matching_prompt = """
        Task:
        Determine if the candidate has worked at or is currently working at any company from the preferred company list.

        Rules:
        - Match if ANY of the following is true (case-insensitive):
          1) Exact company name match.
          2) Common variations/abbreviations (e.g., "Google" matches "Google Inc.", "Google LLC", "Alphabet/Google").
          3) Parent company or well-known subsidiary relationship (e.g., "Instagram" matches "Meta", "YouTube" matches "Google").
          4) Acquired companies that are commonly associated (e.g., "LinkedIn" matches "Microsoft").
        - Check ALL work experiences in the candidate's resume (current and past positions).
        - If the candidate has worked at ANY company in the preferred list at ANY point, return true.
        - If unclear or company names are not recognizable, return false.
        - "is_current_employee": true if the candidate is CURRENTLY employed at a matched preferred company.
        - "is_previous_employee": true if the candidate has PREVIOUSLY worked at a matched preferred company but is NOT currently there.

        Input:
        Preferred Company List: {preferred_company_list}
        Candidate Resume: {candidate_resume}

        Output (JSON only in a Markdown code block; no extra text):
        ```json
        {{
          "reason": "<1–2 concise sentences explaining the match or why no match>",
          "matched_companies": ["<list of matched companies from preferred list, empty if none>"],
          "candidate_companies": ["<list of companies found in resume that matched>"],
          "is_current_employee": boolean,
          "is_previous_employee": boolean,
          "company_matching": boolean
        }}
        ```
    """

company_synonym_check_prompt_template = """
    Given the raw company name and a list of possible LinkedIn company matches, select the one that most likely refers to the same company.
    Raw company name: {raw_company}
    Possible matches: {synonym_list}

    Return ONLY the exact company name from the list that best matches the raw company name. Do not add any explanation."""


location_synonym_check_prompt_template = """
    Task
    You are given an extracted job location and a list of candidate LinkedIn
    location names returned by LinkedIn's location typeahead. Choose the SINGLE
    candidate that best represents this place as a LinkedIn search region for
    sourcing candidates.

    Extracted location: {raw_location}
    Candidates: {synonym_list}

    Selection rules (in priority order):
    1) Prefer the canonical metropolitan-area / greater-area form that LinkedIn
       actually uses for this place (e.g. "<City> Metropolitan Area",
       "Greater <City> Area", "Greater <City> Metropolitan Region",
       "<City> Bay Area"). A metro form covers a wider, more useful talent pool
       than the bare city.
    2) Prefer a candidate that names the CITY (+ country) and does NOT embed a
       sub-national state / province / region name. For example, prefer
       "Kota Kinabalu, Malaysia Metropolitan Area" over
       "Greater Kota Kinabalu, Sabah, Malaysia" — the latter embeds the state
       "Sabah", which is not how LinkedIn canonically names the metro.
    3) Never choose a bare country (e.g. "Malaysia") or a bare state/province
       (e.g. "Sabah") when a city-level or metro candidate exists.
    4) Pick exactly ONE candidate, copied CHARACTER-FOR-CHARACTER from the
       Candidates list.

    Output
    Return ONLY the chosen candidate string — no quotes, no explanation."""