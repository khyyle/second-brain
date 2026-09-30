import SwiftUI

/// The wiki's domain vocabulary, with rename / merge / delete. Domains are
/// frontmatter metadata the compilation agent grows over time; this is where
/// the user curates that vocabulary without editing the wiki by hand.
struct DomainsTab: View {
    let config: AppConfig
    @State private var domains: [DomainInfo] = []
    @State private var loaded = false
    @State private var unavailable = false
    @State private var busy = false
    @State private var renaming: DomainInfo?
    @State private var renameText = ""
    @State private var deleting: DomainInfo?

    var body: some View {
        VStack(spacing: 1) {
            header
            content
        }
        .onAppear(perform: refresh)
        .onPanelShow(refresh)
        .alert("Rename domain", isPresented: renamePresented) {
            TextField("New name", text: $renameText)
            Button("Rename", action: performRename)
            Button("Cancel", role: .cancel) { renaming = nil }
        } message: {
            Text(renaming.map { "Rename '\($0.name)' across \($0.pageCount) page(s)." } ?? "")
        }
        .alert("Delete domain", isPresented: deletePresented) {
            Button("Delete", role: .destructive, action: performDelete)
            Button("Cancel", role: .cancel) { deleting = nil }
        } message: {
            Text(
                deleting.map {
                    "Remove '\($0.name)' from \($0.pageCount) page(s)? The pages will stay, "
                        + "only this domain is removed from their frontmatter."
                } ?? ""
            )
        }
    }

    private var header: some View {
        HStack(spacing: 5) {
            Text("Domains")
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.Colors.textTertiary)
            HelpButton(
                text: "Domains are broad subject areas tagged in each page's frontmatter, "
                    + "added as needed by the compilation agent during builds."
            )
            Spacer()
            if busy { ProgressView().controlSize(.small).scaleEffect(0.7) }
        }
        .padding(.horizontal, 8).padding(.top, 4).padding(.bottom, 1)
    }

    @ViewBuilder
    private var content: some View {
        if !loaded {
            EmptyListMessage(text: nil)
        } else if unavailable {
            EmptyListMessage(text: "Domains need the installed pipeline. Reinstall to manage them.")
        } else if domains.isEmpty {
            EmptyListMessage(text: "No domains yet. They appear once you build the wiki.")
        } else {
            ForEach(domains) { domain in
                DomainRow(
                    domain: domain,
                    others: domains.map(\.name).filter { $0 != domain.name },
                    busy: busy,
                    onOpen: { open(domain) },
                    onRename: { startRename(domain) },
                    onMerge: { merge(domain, into: $0) },
                    onDelete: { deleting = domain }
                )
            }
        }
    }

    private var renamePresented: Binding<Bool> {
        Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })
    }

    private var deletePresented: Binding<Bool> {
        Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })
    }

    private func startRename(_ domain: DomainInfo) {
        renameText = domain.name
        renaming = domain
    }

    private func performRename() {
        guard let target = renaming else { return }
        let newName = renameText.trimmingCharacters(in: .whitespacesAndNewlines)
        renaming = nil
        guard !newName.isEmpty, newName != target.name else { return }
        mutate { DomainMutator.rename(config: config, old: target.name, new: newName, completion: $0) }
    }

    private func performDelete() {
        guard let target = deleting else { return }
        deleting = nil
        mutate { DomainMutator.delete(config: config, name: target.name, completion: $0) }
    }

    private func merge(_ domain: DomainInfo, into dest: String) {
        mutate {
            DomainMutator.merge(config: config, sources: [domain.name], dest: dest, completion: $0)
        }
    }

    /// Run a mutation with the busy spinner up, then refresh the list.
    private func mutate(_ action: (@escaping (Bool) -> Void) -> Void) {
        busy = true
        action { _ in
            busy = false
            refresh()
        }
    }

    private func open(_ domain: DomainInfo) {
        openInDefaultApp(config.wikiRoot.appending(path: "_views/domains/\(domain.name).md"))
    }

    private func refresh() {
        guard config.repoDir != nil else {
            unavailable = true
            loaded = true
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            let result = DomainData.load(config: config)
            DispatchQueue.main.async {
                if let result {
                    domains = result
                    unavailable = false
                } else {
                    unavailable = true
                }
                loaded = true
            }
        }
    }
}
