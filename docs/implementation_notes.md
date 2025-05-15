### **Step 2: Dynamic URL sql Scraping Task** (currently the scraper selects urls from data/scraping folders but we need to change this to the following:)

* **Task:**
  Select URLs dynamically from `companies.website` sql table column for scraping.
* **Implementation details:**

  * Allow specifying how many URLs to scrape per run (e.g., 100, 500, 19000).
  * Allow to pass args such as --scrape-mode, --run-name etc.
  * Check `scraping_logs` to avoid rescraping URLs already completed successfully.
  * Mark each scrape clearly (`pending`, `completed`, `failed`).

---

### **Step 3: Scraping Process**

* **Task:**
  For each URL:

  1. Scrape the HTML content + images when included. 
  2. Extract plain text from HTML + images. 
  3. Save locally: (we already do this)

     * HTML to `data/scraping/<timestamp_or_run_name>/pages/<company_name>/page.html`
     * Images to `data/scraping/<timestamp_or_run_name>/images/<company_name>/`
     * OCR summary to `data/scraping/<timestamp_or_run_name>/pages/<company_name>/ocr/summary.json`
     * Plain text to `data/scraping/<timestamp_or_run_name>/pages/<company_name>/text.txt`
     * Plain JSON to `data/scraping/<timestamp_or_run_name>/pages/<company_name>/text.json`
     * Session log to `data/scraping/<timestamp_or_run_name>/session.log`
     * Summary JSON to `data/scraping/<timestamp_or_run_name>/summary.json`

  4. Update sql database tables:

     * `scraping_logs` table: 
            log_id UUID PRIMARY KEY,
            client_id UUID REFERENCES companies(client_id),
            source TEXT, -- homepage, about, linkedin
            url_scraped TEXT,
            status TEXT, -- pending, completed, failed
            scraping_date TIMESTAMP DEFAULT NOW(),
            error_message TEXT

     * `scraped_pages` table: 
            page_id UUID PRIMARY KEY,
            client_id UUID REFERENCES companies(client_id),
            url TEXT NOT NULL,
            page_type TEXT, -- homepage, support, linkedin, about, etc.
            scraped_at TIMESTAMP DEFAULT NOW(),
            raw_html_path TEXT,
            plain_text_path TEXT,
            summary TEXT,
            extraction_notes TEXT

(URL from companies.website, paths to saved files, `client_id`, timestamp, and any notes.)

**Example Database Inserts:**

```sql
-- scraped_pages example insert:
INSERT INTO scraped_pages (
    page_id, client_id, url, page_type, scraped_at,
    raw_html_path, plain_text_path, summary, extraction_notes
) VALUES (
    gen_random_uuid(), :client_id, :url, 'homepage', NOW(),
    :raw_html_path, :plain_text_path, 'Scraped successfully', NULL
);

-- scraping_logs example insert:
INSERT INTO scraping_logs (
    log_id, client_id, source, url_scraped, status,
    scraping_date, error_message
) VALUES (
    gen_random_uuid(), :client_id, 'homepage', :url, 'completed',
    NOW(), NULL
);
```




## 🗂️ **Database Tables for Reference**

**`companies`** 
CREATE TABLE companies (
    -- Identity
    company_name TEXT,

    -- Contact / Online Presence (grouped high)
    website TEXT,
    company_phone TEXT,
    linkedin_url TEXT,
    facebook_url TEXT,
    twitter_url TEXT,

    -- Business Metadata
    industry TEXT,
    employees INTEGER,
    founded_year INTEGER,
    annual_revenue TEXT,
    number_of_retail_locations INTEGER,

    -- Descriptions & Keywords
    short_description TEXT,
    seo_description TEXT,
    keywords TEXT,
    technologies TEXT,

    -- Address Info (grouped together)
    street TEXT,
    city TEXT,
    state TEXT,
    postal_code TEXT,
    country TEXT,
    address TEXT,

    -- External ID
    apollo_account_id TEXT
);



**`scraped_pages`**

```sql
page_id UUID PRIMARY KEY,
client_id UUID REFERENCES companies(client_id),
url TEXT NOT NULL,
page_type TEXT, -- homepage, support, linkedin, about, etc.
scraped_at TIMESTAMP DEFAULT NOW(),
raw_html_path TEXT,
plain_text_path TEXT,
summary TEXT,
extraction_notes TEXT
```

**`scraping_logs`**

```sql
log_id UUID PRIMARY KEY,
client_id UUID REFERENCES companies(client_id),
source TEXT, -- homepage, about, linkedin
url_scraped TEXT,
status TEXT, -- pending, completed, failed
scraping_date TIMESTAMP DEFAULT NOW(),
error_message TEXT
```


```
## ⚙️ **Overall System Flow Summary**

```

(1) Dynamically select URLs to scrape → scraping_logs table
(2) Scrape URLs & save locally → scraped_pages + scraping_logs tables