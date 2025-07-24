from scholarly import scholarly
from tabulate import tabulate

scholar_id = "9pl2QhIAAAAJ"  # Replace with your scholar ID

try:
    author = scholarly.search_author_id(scholar_id)
    author = scholarly.fill(author, sections=["publications"])
    print(f"Author: {author['name']}\n")

    table_data = []
    for pub in author["publications"]:
        try:
            pub_data = scholarly.fill(pub)

            title = pub_data.get("bib", {}).get("title", "")
            authors = pub_data.get("bib", {}).get("author", "")
            year = pub_data.get("bib", {}).get("pub_year", "")
            citations = pub_data.get("num_citations", 0)

            table_data.append([title, authors, year, citations])

        except Exception as e:
            print("⚠️ Skipping a publication due to error:", e)
            continue

    # Print the table
    headers = ["Title", "Authors", "Year", "Citations"]
    print(tabulate(table_data, headers=headers, tablefmt="grid"))

    print(f"\n✅ Total Valid Publications: {len(table_data)}")

except Exception as e:
    print("❌ Error:", e)
