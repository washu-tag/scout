[Reference the ticket you are addressing and provide a succinct and descriptive title for the pull request, e.g., "SCOUT-12 Improve caching mechanism for API calls"]

## Type of change
- [ ] New feature
- [ ] Improvement
- [ ] Bug fix
- [ ] Refactor
- [ ] Documentation update
- [ ] Test update

## Product description
[Provide a summary explanation of your changes from a product/user perspective. More details should be found in your updates to the user docs.]

## Technical description
[Provide a summary explanation of your changes from a technical perspective. More details should be found in your updates to the technical docs.]

## Impact

### Security 

##### Authorization
[Do your changes add or modify user roles? Do they impact the data users are able to see, or the actions they are able to take? Could a user modify a REST API path and see data they aren't supposed to see? Were you mindful of least privilege?]

##### Appsec
[Did you review your changes with application security in mind? See the [OWASP list](https://github.com/0xRadi/OWASP-Web-Checklist).]

### Performance
[At what scale do we expect this to operate? How have you verified that it can do so?]

### Data
[Did you consider any edge cases around input data (e.g., DICOM type 2 elements are required to be present but may be empty)? Are you gracefully handling errors from "bad data," e.g., non-conforming DICOM?]

### Backward compatibility
[Any changes to the data model, APIs, dependencies?]

## Test description
[Detail the testing you have performed to ensure that these changes function as intended. Include information about the test automation you've added, as well as the manual testing you've performed. Be sure to reference areas of impact, above.]

## Note for reviewers
[Any additional information or direction for reviewers.]

## Checklist
- [ ] My code adheres to the coding and style guidelines of the project.
- [ ] I have commented my code, particularly in hard-to-understand areas.
- [ ] I have added or updated user documentation, if appropriate.
- [ ] I have added or updated technical documentation, including an architectural decision record, if appropriate.
- [ ] I have added unit tests, unless this is a test code PR.
- [ ] I have added end-to-end tests, unless this is a documentation-only PR.
