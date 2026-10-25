# Flight links when fares are unavailable

1. The configured fare-provider adapter is checked first. SerpAPI is optional and is not required to start the app.
2. If it is not configured, or a provider error prevents flight offers, the Flight Agent asks Tavily for up to five relevant pages using the submitted origin, destination, departure date, return date/one-way intent, and airline-booking terms.
3. URLs are validated before display, duplicate URLs are removed, and only returned Tavily URLs are displayed.
4. The response uses `flight_search_links` and labels every item `external_search_page_not_confirmed_offer`. It does not create fake flight cards, invent flight numbers, or imply prices or availability.
5. The Google Flights route-search URL is also available as a fallback. It is a search page, not a booking link for a particular flight.

**Important:** Tavily is a web search/research API. A search result can be a route-search page, airline page, OTA page, article, or other page. It is not a guaranteed deep link to a bookable flight. Direct flight-specific offer links and prices require a provider that returns that offer data.
