---
description: Create a new feature specification from user requirements using the spec template.
scripts:
  sh: scripts/bash/create-new-feature.sh --json
  ps: scripts/powershell/create-new-feature.ps1 -Json
---
$ARGUMENTS

The text the user typed after `/specify` in the triggering message **is** the feature description. Assume you always have it available in this conversation even if `{ARGS}` appears literally below. Do not ask the user to repeat it unless they provided an empty command.

**ENHANCED SPECIFICATION PROCESS** - For complex, multi-domain features requiring iterative refinement:

## Phase 1: Initial Analysis & Research Trigger
1. **Analyze complexity** - Determine if this is a simple feature or complex architecture requiring research:
   - **Simple**: Direct UI components, basic CRUD, straightforward workflows
   - **Complex**: Multi-domain systems, AI integration, cross-cutting concerns, Vietnamese/multilingual, master agent pipelines, knowledge synthesis platforms

2. **For COMPLEX features** - Trigger research phase:
   - Use available MCP tools (Context7, Tavily, Deep Wiki) to research domain patterns
   - For Vietnamese platforms: Research Vietnamese NLP libraries, tokenization, UI patterns
   - For AI platforms: Research current AI SDK patterns, orchestration frameworks
   - For knowledge synthesis: Research document processing, vector databases, content pipeline patterns

## Phase 2: Iterative Specification Development
1. Run the script `{SCRIPT}` from repo root and parse its JSON output for BRANCH_NAME and SPEC_FILE. All file paths must be absolute.
   **IMPORTANT** You must only ever run this script once. The JSON is provided in the terminal as output - always refer to it to get the actual content you're looking for.

2. Load `templates/spec-template.md` to understand required sections.

3. **Enhanced specification writing process**:
   - **Domain Context Integration**: Embed research findings into specification context
   - **Stakeholder Flow Mapping**: For complex platforms, map different user types and their interaction flows
   - **Cross-cutting Concern Identification**: Identify authentication, data flow, integration points
   - **Use Case Matrix Generation**: Generate comprehensive scenarios based on feature complexity
   - **Constraint & Integration Analysis**: Document technical constraints and integration requirements

4. **Iterative Refinement Check**:
   - Mark areas needing clarification with `[NEEDS CLARIFICATION: specific question]`
   - For complex features, generate follow-up questions for domain-specific details
   - Suggest running `/clarify` for complex features before proceeding to `/plan`

5. Report completion with branch name, spec file path, complexity assessment, and readiness for the next phase.

**COMPLEXITY INDICATORS** requiring enhanced process:
- Multi-language/localization requirements
- AI agent orchestration or master agent systems  
- Cross-domain data processing (documents, video, audio)
- Real-time systems or complex state management
- Integration with multiple external APIs or frameworks
- Knowledge synthesis or content transformation pipelines

Note: The script creates and checks out the new branch and initializes the spec file before writing.
