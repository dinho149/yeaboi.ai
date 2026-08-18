// Typed views over the parsed namespace.
//
// Python twin: the argparse.Namespace produced by cli.build_parser() —
// Python reads attributes dynamically; the Go dispatch (arriving with the
// command implementations in later waves) reads these structs. Pointer
// fields are the dests whose parse can produce None; value fields always
// hold something (argparse supplies their default).
package main

// Args is the top-level namespace: every flat flag, plus which subcommand
// ran (Command == "" mirrors command=None) and its typed view.
type Args struct {
	Resume                  *string
	ListSessions            bool
	ClearSessions           bool
	ExportQuestionnaire     *string
	Questionnaire           *string
	Quick                   bool
	ExportOnly              bool
	PriorArt                []string
	ACFormat                *string
	ArchitectureSpike       *string
	NoBell                  bool
	Theme                   string
	Mode                    *string
	Setup                   bool
	AllowPath               []string
	ListAudioDevices        bool
	InstallVoice            bool
	DryRun                  bool
	NonInteractive          bool
	Output                  *string
	Description             *string
	TeamSize                *int64
	SprintLength            *int64
	StandupRun              bool
	StandupSession          *string
	StandupOutput           *string
	StandupInteractive      bool
	StandupRemindTranscript bool
	Learn                   bool
	TeamProfile             bool
	Retro                   *string

	Command string

	Report        *ReportArgs
	Standup       *StandupArgs
	StandupReview *StandupReviewArgs
	Perf          *PerfArgs
	RetroCmd      *RetroPokerArgs
	Poker         *RetroPokerArgs
	Ceremonies    *CeremoniesArgs
	Agents        *AgentsArgs
	Provenance    *ProvenanceArgs
	Ship          *ShipArgs
	Analyze       *AnalyzeArgs
}

// ReportArgs is `yeaboi report`.
type ReportArgs struct {
	Period               string
	Session              string
	WindowStart          string
	WindowEnd            string
	SprintNames          string
	Label                string
	JiraProject          string
	AzdoProject          string
	Source               string
	CodeSources          []string
	DocumentationSources []string
	Strict               bool
	Format               string
	Theme                string
}

// StandupArgs is `yeaboi standup`.
type StandupArgs struct {
	Session                    string
	Deliver                    bool
	Channels                   []string
	Days                       int64
	TrackerSources             []string
	TeamMembers                []string
	CodeSources                []string
	GithubOwners               []string
	GithubRepositories         []string
	GithubExcludedRepositories []string
	AzdoProjects               []string
	AzdoRepositories           []string
	DocumentationSources       []string
	ListMembers                bool
	Schedule                   *string
	ReviewTranscripts          bool
	Strict                     bool
	Format                     string
}

// StandupReviewArgs is `yeaboi standup-review`.
type StandupReviewArgs struct {
	Session         string
	Paths           []string
	TranscriptPaths []string
	TranscriptText  string
	TranscriptDir   string
	StandupDate     string
	MaxTranscripts  int64
	IncludeReviewed bool
	FileIssues      bool
	ListGaps        bool
	Strict          bool
	Format          string
}

// PerfArgs is `yeaboi perf <command>`.
type PerfArgs struct {
	Command     string
	Engineer    string // prep/complete/review/note
	Session     string
	JiraProject string
	AzdoProject string
	Strict      bool
	Transcript  string // complete
	Deliver     bool
	Images      []string
	Recipients  []string
	Months      int64  // review
	Text        string // note
}

// RetroPokerArgs is `yeaboi retro` / `yeaboi poker`.
type RetroPokerArgs struct {
	Session string
	Limit   int64
	Export  bool
	Format  string
}

// CeremoniesArgs is `yeaboi ceremonies <command>`.
type CeremoniesArgs struct {
	Command    string
	Name       string
	Session    string
	Format     string
	Mode       string // add
	At         string
	Weekdays   string
	Channels   string
	Arg        []string
	StaleAfter int64
	MonthlyCap float64
	Scheduled  bool // run
	DryRun     bool
	Limit      int64 // history
}

// AgentsArgs is `yeaboi agents <command>`.
type AgentsArgs struct {
	Command              string
	WindowDays           int64 // cost/advisor
	Project              string
	Source               string
	Format               string
	Strict               bool
	Days                 *int64 // standup
	TrackerSources       []string
	GithubOwners         []string
	AzdoProjects         []string
	IncludeLocalSessions bool
	Deliver              bool
	Deep                 bool // security
}

// ProvenanceArgs is `yeaboi provenance <command>`.
type ProvenanceArgs struct {
	Command    string
	WindowDays int64 // audit
	EntityID   string
	Depth      int64 // trace
	Format     string
	Strict     bool
}

// ShipArgs is `yeaboi ship <command>`.
type ShipArgs struct {
	Command        string
	StoryID        string
	Repo           string
	Session        string
	Check          string
	TimeoutMinutes int64
	DryRun         bool
	Format         string
	Strict         bool
	Limit          int64
}

// AnalyzeArgs is `yeaboi analyze`.
type AnalyzeArgs struct {
	Source            string
	Project           string
	Sprints           int64
	Depth             string
	WindowDays        int64
	AnalysisModel     *string
	Features          []string
	GithubOwner       []string
	AzdoCodeProject   []string
	ConfluenceSpace   []string
	NotionRoot        []string
	Samples           bool
	NoInsights        bool
	IncludeAIUsage    bool
	IncludeDocQuality bool
	Delivery          []string
	Code              []string
	Docs              []string
	Members           []string
	Strict            bool
	Format            string
}

func nsStr(ns map[string]any, key string) string {
	s, _ := ns[key].(string)
	return s
}

func nsStrPtr(ns map[string]any, key string) *string {
	if s, ok := ns[key].(string); ok {
		return &s
	}
	return nil
}

func nsBool(ns map[string]any, key string) bool {
	b, _ := ns[key].(bool)
	return b
}

func nsInt(ns map[string]any, key string) int64 {
	i, _ := ns[key].(int64)
	return i
}

func nsIntPtr(ns map[string]any, key string) *int64 {
	if i, ok := ns[key].(int64); ok {
		return &i
	}
	return nil
}

func nsFloat(ns map[string]any, key string) float64 {
	f, _ := ns[key].(float64)
	return f
}

// nsStrList distinguishes None (nil) from an empty list, like Python does.
func nsStrList(ns map[string]any, key string) []string {
	list, ok := ns[key].([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(list))
	for _, v := range list {
		if s, ok := v.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

// fromNamespace builds the typed view from a successful parse.
func fromNamespace(ns map[string]any) *Args {
	a := &Args{
		Resume:                  nsStrPtr(ns, "resume"),
		ListSessions:            nsBool(ns, "list_sessions"),
		ClearSessions:           nsBool(ns, "clear_sessions"),
		ExportQuestionnaire:     nsStrPtr(ns, "export_questionnaire"),
		Questionnaire:           nsStrPtr(ns, "questionnaire"),
		Quick:                   nsBool(ns, "quick"),
		ExportOnly:              nsBool(ns, "export_only"),
		PriorArt:                nsStrList(ns, "prior_art"),
		ACFormat:                nsStrPtr(ns, "ac_format"),
		ArchitectureSpike:       nsStrPtr(ns, "architecture_spike"),
		NoBell:                  nsBool(ns, "no_bell"),
		Theme:                   nsStr(ns, "theme"),
		Mode:                    nsStrPtr(ns, "mode"),
		Setup:                   nsBool(ns, "setup"),
		AllowPath:               nsStrList(ns, "allow_path"),
		ListAudioDevices:        nsBool(ns, "list_audio_devices"),
		InstallVoice:            nsBool(ns, "install_voice"),
		DryRun:                  nsBool(ns, "dry_run"),
		NonInteractive:          nsBool(ns, "non_interactive"),
		Output:                  nsStrPtr(ns, "output"),
		Description:             nsStrPtr(ns, "description"),
		TeamSize:                nsIntPtr(ns, "team_size"),
		SprintLength:            nsIntPtr(ns, "sprint_length"),
		StandupRun:              nsBool(ns, "standup_run"),
		StandupSession:          nsStrPtr(ns, "standup_session"),
		StandupOutput:           nsStrPtr(ns, "standup_output"),
		StandupInteractive:      nsBool(ns, "standup_interactive"),
		StandupRemindTranscript: nsBool(ns, "standup_remind_transcript"),
		Learn:                   nsBool(ns, "learn"),
		TeamProfile:             nsBool(ns, "team_profile"),
		Retro:                   nsStrPtr(ns, "retro"),
		Command:                 nsStr(ns, "command"),
	}
	switch a.Command {
	case "report":
		a.Report = &ReportArgs{
			Period:               nsStr(ns, "period"),
			Session:              nsStr(ns, "session"),
			WindowStart:          nsStr(ns, "window_start"),
			WindowEnd:            nsStr(ns, "window_end"),
			SprintNames:          nsStr(ns, "sprint_names"),
			Label:                nsStr(ns, "label"),
			JiraProject:          nsStr(ns, "jira_project"),
			AzdoProject:          nsStr(ns, "azdo_project"),
			Source:               nsStr(ns, "source"),
			CodeSources:          nsStrList(ns, "code_sources"),
			DocumentationSources: nsStrList(ns, "documentation_sources"),
			Strict:               nsBool(ns, "strict"),
			Format:               nsStr(ns, "format"),
			Theme:                nsStr(ns, "theme"),
		}
	case "standup":
		a.Standup = &StandupArgs{
			Session:                    nsStr(ns, "session"),
			Deliver:                    nsBool(ns, "deliver"),
			Channels:                   nsStrList(ns, "channels"),
			Days:                       nsInt(ns, "days"),
			TrackerSources:             nsStrList(ns, "tracker_sources"),
			TeamMembers:                nsStrList(ns, "team_members"),
			CodeSources:                nsStrList(ns, "code_sources"),
			GithubOwners:               nsStrList(ns, "github_owners"),
			GithubRepositories:         nsStrList(ns, "github_repositories"),
			GithubExcludedRepositories: nsStrList(ns, "github_excluded_repositories"),
			AzdoProjects:               nsStrList(ns, "azdo_projects"),
			AzdoRepositories:           nsStrList(ns, "azdo_repositories"),
			DocumentationSources:       nsStrList(ns, "documentation_sources"),
			ListMembers:                nsBool(ns, "list_members"),
			Schedule:                   nsStrPtr(ns, "schedule"),
			ReviewTranscripts:          nsBool(ns, "review_transcripts"),
			Strict:                     nsBool(ns, "strict"),
			Format:                     nsStr(ns, "format"),
		}
	case "standup-review":
		a.StandupReview = &StandupReviewArgs{
			Session:         nsStr(ns, "session"),
			Paths:           nsStrList(ns, "paths"),
			TranscriptPaths: nsStrList(ns, "transcript_paths"),
			TranscriptText:  nsStr(ns, "transcript_text"),
			TranscriptDir:   nsStr(ns, "transcript_dir"),
			StandupDate:     nsStr(ns, "standup_date"),
			MaxTranscripts:  nsInt(ns, "max_transcripts"),
			IncludeReviewed: nsBool(ns, "include_reviewed"),
			FileIssues:      nsBool(ns, "file_issues"),
			ListGaps:        nsBool(ns, "list_gaps"),
			Strict:          nsBool(ns, "strict"),
			Format:          nsStr(ns, "format"),
		}
	case "perf":
		a.Perf = &PerfArgs{
			Command:     nsStr(ns, "perf_command"),
			Engineer:    nsStr(ns, "engineer"),
			Session:     nsStr(ns, "session"),
			JiraProject: nsStr(ns, "jira_project"),
			AzdoProject: nsStr(ns, "azdo_project"),
			Strict:      nsBool(ns, "strict"),
			Transcript:  nsStr(ns, "transcript"),
			Deliver:     nsBool(ns, "deliver"),
			Images:      nsStrList(ns, "images"),
			Recipients:  nsStrList(ns, "recipients"),
			Months:      nsInt(ns, "months"),
			Text:        nsStr(ns, "text"),
		}
	case "retro":
		a.RetroCmd = &RetroPokerArgs{
			Session: nsStr(ns, "session"),
			Limit:   nsInt(ns, "limit"),
			Export:  nsBool(ns, "export"),
			Format:  nsStr(ns, "format"),
		}
	case "poker":
		a.Poker = &RetroPokerArgs{
			Session: nsStr(ns, "session"),
			Limit:   nsInt(ns, "limit"),
			Export:  nsBool(ns, "export"),
			Format:  nsStr(ns, "format"),
		}
	case "ceremonies":
		a.Ceremonies = &CeremoniesArgs{
			Command:    nsStr(ns, "ceremonies_command"),
			Name:       nsStr(ns, "name"),
			Session:    nsStr(ns, "session"),
			Format:     nsStr(ns, "format"),
			Mode:       nsStr(ns, "mode"),
			At:         nsStr(ns, "at"),
			Weekdays:   nsStr(ns, "weekdays"),
			Channels:   nsStr(ns, "channels"),
			Arg:        nsStrList(ns, "arg"),
			StaleAfter: nsInt(ns, "stale_after"),
			MonthlyCap: nsFloat(ns, "monthly_cap"),
			Scheduled:  nsBool(ns, "scheduled"),
			DryRun:     nsBool(ns, "dry_run"),
			Limit:      nsInt(ns, "limit"),
		}
	case "agents":
		a.Agents = &AgentsArgs{
			Command:              nsStr(ns, "agents_command"),
			WindowDays:           nsInt(ns, "window_days"),
			Project:              nsStr(ns, "project"),
			Source:               nsStr(ns, "source"),
			Format:               nsStr(ns, "format"),
			Strict:               nsBool(ns, "strict"),
			Days:                 nsIntPtr(ns, "days"),
			TrackerSources:       nsStrList(ns, "tracker_sources"),
			GithubOwners:         nsStrList(ns, "github_owners"),
			AzdoProjects:         nsStrList(ns, "azdo_projects"),
			IncludeLocalSessions: nsBool(ns, "include_local_sessions"),
			Deliver:              nsBool(ns, "deliver"),
			Deep:                 nsBool(ns, "deep"),
		}
	case "provenance":
		a.Provenance = &ProvenanceArgs{
			Command:    nsStr(ns, "provenance_command"),
			WindowDays: nsInt(ns, "window_days"),
			EntityID:   nsStr(ns, "entity_id"),
			Depth:      nsInt(ns, "depth"),
			Format:     nsStr(ns, "format"),
			Strict:     nsBool(ns, "strict"),
		}
	case "ship":
		a.Ship = &ShipArgs{
			Command:        nsStr(ns, "ship_command"),
			StoryID:        nsStr(ns, "story_id"),
			Repo:           nsStr(ns, "repo"),
			Session:        nsStr(ns, "session"),
			Check:          nsStr(ns, "check"),
			TimeoutMinutes: nsInt(ns, "timeout_minutes"),
			DryRun:         nsBool(ns, "dry_run"),
			Format:         nsStr(ns, "format"),
			Strict:         nsBool(ns, "strict"),
			Limit:          nsInt(ns, "limit"),
		}
	case "analyze":
		a.Analyze = &AnalyzeArgs{
			Source:            nsStr(ns, "source"),
			Project:           nsStr(ns, "project"),
			Sprints:           nsInt(ns, "sprints"),
			Depth:             nsStr(ns, "depth"),
			WindowDays:        nsInt(ns, "window_days"),
			AnalysisModel:     nsStrPtr(ns, "analysis_model"),
			Features:          nsStrList(ns, "features"),
			GithubOwner:       nsStrList(ns, "github_owner"),
			AzdoCodeProject:   nsStrList(ns, "azdo_code_project"),
			ConfluenceSpace:   nsStrList(ns, "confluence_space"),
			NotionRoot:        nsStrList(ns, "notion_root"),
			Samples:           nsBool(ns, "samples"),
			NoInsights:        nsBool(ns, "no_insights"),
			IncludeAIUsage:    nsBool(ns, "include_ai_usage"),
			IncludeDocQuality: nsBool(ns, "include_doc_quality"),
			Delivery:          nsStrList(ns, "delivery"),
			Code:              nsStrList(ns, "code"),
			Docs:              nsStrList(ns, "docs"),
			Members:           nsStrList(ns, "members"),
			Strict:            nsBool(ns, "strict"),
			Format:            nsStr(ns, "format"),
		}
	}
	return a
}
