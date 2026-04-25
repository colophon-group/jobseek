# Phase 2-2: Resume Customization Implementation Plan

## Overview
Enable users to customize their LaTeX resume based on missing keywords from job fit analysis. Uses Claude Sonnet 4.6 (primary) + GPT-4o (fallback) for intelligent keyword integration while maintaining formatting and authenticity.

## Key Requirements
- **Model Strategy**: Sonnet 4.6 primary → GPT-4o fallback
- **Format**: LaTeX (.tex) source modification only
- **Constraints**: One-page limit, preserve formatting/alignment
- **Strategy**: Semantic keyword replacement (e.g., Java→Kotlin, Python→Go for compatible stacks)
- **Scope**: Prioritize first experience section for maximum impact
- **Validation**: No false pairings (e.g., Python ✗ Spring Boot)

## Implementation Tasks

### Phase 2-2A: Resume Customization Server Logic
1. **Task 1**: Create `customizeResume` server action
   - Input: queueId, missing keywords, posting title
   - Output: customized LaTeX content + diff preview
   - Call Claude Sonnet 4.6 with LaTeX-specific prompting
   - Fallback to GPT-4o if Sonnet unavailable
   - Return both customized content and preview

2. **Task 2**: Create `saveCustomization` server action
   - Save customized LaTeX to storage (R2)
   - Update user_resume table with customized_at timestamp
   - Track customization history

3. **Task 3**: Add resume diff/preview component
   - Display side-by-side before/after
   - Highlight inserted keywords
   - Show suggested changes

### Phase 2-2B: UI Components
4. **Task 4**: Create `ResumeCustomizationModal` component
   - Triggered from queue job card
   - Shows customization options + preview
   - Accept/reject flow

5. **Task 5**: Wire customization to queue
   - Add "Customize Resume" button to QueueJobCard
   - Open modal with job context
   - Handle save and re-render

### Phase 2-2C: Testing & Validation
6. **Task 6**: Build verification + E2E testing
   - Test customization flow end-to-end
   - Validate LaTeX syntax preservation
   - Verify formatting maintained

## Testing Strategy
- **Unit tests**: LaTeX parsing, keyword matching logic
- **Integration tests**: Server actions with real resume
- **E2E tests**: Full customization flow with UI
- **Target coverage**: 80% for each module

## Success Criteria
✅ Successful resume customization that preserves formatting
✅ Meaningful keyword suggestions (no false pairings)
✅ Both Sonnet and GPT-4o working
✅ One-page constraint maintained
✅ Full test coverage (80%+)
