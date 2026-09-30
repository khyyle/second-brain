import AppKit

/// The panel's content view. It hosts the SwiftUI tree and is the window's one
/// destination for dragged files, so files dropped on any tab are taken the
/// same way as files dropped on the drop card.
///
/// Drags are handled in AppKit because SwiftUI only reveals dragged files once
/// they are dropped, too late to show whether they will be accepted.
final class DropContainerView: NSView {
    private let intake: DropIntake
    private var dragIsAcceptable = false

    init(content: NSView, intake: DropIntake) {
        self.intake = intake
        super.init(frame: .zero)
        content.translatesAutoresizingMaskIntoConstraints = false
        addSubview(content)
        NSLayoutConstraint.activate([
            content.leadingAnchor.constraint(equalTo: leadingAnchor),
            content.trailingAnchor.constraint(equalTo: trailingAnchor),
            content.topAnchor.constraint(equalTo: topAnchor),
            content.bottomAnchor.constraint(equalTo: bottomAnchor),
        ])
        registerForDraggedTypes([.fileURL])
    }

    required init?(coder: NSCoder) {
        nil
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        dragIsAcceptable = intake.dragEntered(fileURLs(in: sender))
        return dragIsAcceptable ? .copy : []
    }

    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
        dragIsAcceptable ? .copy : []
    }

    override func draggingExited(_ sender: NSDraggingInfo?) {
        intake.dragEnded()
    }

    override func draggingEnded(_ sender: NSDraggingInfo) {
        intake.dragEnded()
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        intake.dragEnded()
        guard dragIsAcceptable else { return false }
        intake.add(fileURLs(in: sender))
        return true
    }

    private func fileURLs(in info: NSDraggingInfo) -> [URL] {
        let objects = info.draggingPasteboard.readObjects(
            forClasses: [NSURL.self],
            options: [.urlReadingFileURLsOnly: true]
        )
        return objects as? [URL] ?? []
    }
}
