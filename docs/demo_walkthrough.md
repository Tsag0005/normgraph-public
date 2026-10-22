# GDPR Article 13 demo walkthrough

This is a short run sheet for showing how the browser workspace turns a legal
provision into Gherkin scenarios and checks those scenarios against a document.
The bundled graph and policy documents let you demonstrate the retrieval and
review setup without building a graph during the meeting.

## Before recording

1. Start the published container using the command in the README and open
   `http://localhost:8080`.
2. Have your own OpenAI API key ready for paid generation and document review.
   Enter it in the site's key field only when you are ready for a model call.
3. Keep the [hand-authored sample Gherkin](gdpr_article13_hand_authored_sample.feature)
   open as a backup. Label it as hand-authored if you use it.

## Suggested recording sequence

1. **Select the built-in corpus.** Choose `gdpr_article13_demo` under
   **Selected corpus**. Explain that this focused corpus contains selected GDPR
   articles relevant to the Article 13 experiment. Its graph was built in
   advance and is available immediately through the local CSV backend. You do
   not need **Build graph** for this demo. **Check local graph** is an optional
   diagnostic; it does not unlock scenario generation.
2. **Choose the provision.** Open **Generate scenarios**, load the free
   provision list, and select `13(2)` (or another Article 13 paragraph). The
   source text for that identifier comes from the corpus.
3. **Preview the extracted graph context.** Click **Preview extracted graph
   context · free**. Point to the retrieved norms, linked provisions,
   conditions, and exceptions in the result. This textual preview shows what
   the graph extracted for the selected clause and what context the generator
   will receive. It is the graph inspection step in this demo; it does not
   make a model call or require an API key.
4. **Generate scenarios.** Enter your API key, choose a model, and click
   **Generate · paid**. Explain that the pipeline retrieves the graph context
   again, combines it with the selected provision, asks the model for Gherkin,
   and records the result in the browser workspace. Generation consumes tokens.
5. **Evaluate against a policy.** From the generated result click
   **Evaluate against a document**. In **Evaluate document**, check one or more
   scenarios under **Choose Gherkins**. Search by scenario name or Then assertion
   to select a small, useful subset. Then select the
   **GitHub Privacy Statement (2017)** preset. You can instead upload or paste
   any legal document. The preset fills the document name and full document
   text without an API call.
6. **Estimate, then review.** Click **Estimate review · no model call** to
   inspect the selected scenario count and estimated token cost. Choose a review model,
   read its token warning, then click **Run document review · paid**. The model
   receives only the selected Gherkins and policy text and returns pass, fail, or partial
   with reasons for each scenario. Review can use many tokens, especially with
   larger documents or high-end models.
7. **Optional comparison.** Repeat the review with the 2018 policy preset to
   show how the same scenarios can be applied to another document version.

## Narration guardrails

- The interface previews extracted graph context as text. It does not draw an
  interactive node-and-edge graph.
- The SPIRES paper and LinkML contributed to the graph construction method;
  they are not the name of this tool.
- The 2017 policy comparison is counterfactual: GDPR became applicable in 2018.
  A model's verdict is evidence for document review, not a legal conclusion or
  a claim that GitHub historically breached GDPR.
- The graph preview, provision list, policy preset selection, and review
  estimate are free of model calls. Generation and actual document review use
  the visitor's API key. Browser workspace data persists in that visitor's
  browser.
