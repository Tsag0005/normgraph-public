Feature: GDPR transparency disclosures in GitHub privacy statements

  # This is a policy-disclosure audit, not a complete GDPR compliance audit.
  # The 2017 policy is tested counterfactually against requirements that became
  # applicable on 25 May 2018; a failure is not a claim of a historical breach.

  @gdpr @article_13_1_a @general
  Scenario: The policy identifies and provides contact details for the controller
    Given a controller collects personal data directly from a data subject
    When the data subject reads the controller's privacy statement
    Then the policy states the legal identity and contact details of the controller

  @gdpr @article_13_1_b @eu_specific
  Scenario: The policy provides contact details for the data protection officer
    Given the controller is required to appoint a data protection officer
    When the data subject reads the controller's privacy statement
    Then the policy states contact details for the data protection officer

  @gdpr @article_13_1_c @general
  Scenario: The policy explains purposes and legal bases for processing
    Given a controller collects personal data directly from a data subject
    When the data subject reads how personal data is used
    Then the policy states the purposes of processing and the legal bases for processing

  @gdpr @article_13_1_d @general
  Scenario: The policy identifies the legitimate interests it relies on
    Given the controller relies on legitimate interests as a legal basis for processing
    When the data subject reads about the legal basis for processing
    Then the policy identifies the legitimate interests pursued by the controller or a third party

  @gdpr @article_13_1_e @general
  Scenario: The policy identifies recipients of personal data
    Given a controller discloses personal data to other parties
    When the data subject reads how personal data is shared
    Then the policy identifies the recipients or categories of recipients

  @gdpr @article_13_1_f @eu_specific
  Scenario: The policy explains international transfers and their safeguards
    Given the controller transfers personal data from the EU to a third country
    When the data subject reads about international transfers
    Then the policy states that transfers occur and identifies the applicable adequacy mechanism or safeguards

  @gdpr @article_13_2_a @general
  Scenario: The policy explains retention periods or criteria
    Given a controller stores personal data
    When the data subject reads about retention
    Then the policy states the retention period or the criteria used to determine it

  @gdpr @article_13_2_b @general
  Scenario: The policy explains the complete set of relevant data-subject rights
    Given a controller collects personal data directly from a data subject
    When the data subject reads about privacy rights
    Then the policy states the rights of access, rectification, erasure, restriction, objection, and data portability

  @gdpr @article_13_2_c @general
  Scenario: The policy completely explains withdrawal of consent
    Given the controller relies on consent for at least one processing activity
    When the data subject reads about withdrawing consent
    Then the policy states that consent may be withdrawn at any time
    And the policy states that withdrawal does not affect the lawfulness of processing before withdrawal

  @gdpr @article_13_2_d @eu_specific
  Scenario: The policy explains the right to complain to a supervisory authority
    Given a controller collects personal data directly from a data subject
    When the data subject reads about complaints
    Then the policy states the right to lodge a complaint with a competent data protection supervisory authority
