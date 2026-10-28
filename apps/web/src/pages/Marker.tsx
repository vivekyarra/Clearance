import { useParams } from "react-router-dom";

/**
 * Printable AprilTag 36h11 id 0. The tag image comes from the official AprilRobotics/apriltag-imgs
 * repository (10 x 10 cells incl. the white border; the black square is 8 cells). Printed at 100% the
 * black square is 160 mm, matching the default Marker.size_m. Measure it after printing.
 */
const TAG = "https://raw.githubusercontent.com/AprilRobotics/apriltag-imgs/master/tag36h11/tag36_11_00000.png";

export default function MarkerPage() {
  const { site = "" } = useParams();
  return (
    <div style={{ background: "#fff", color: "#000", minHeight: "100%", padding: "12mm", fontFamily: "Inter, system-ui, sans-serif" }}>
      <style>{`@page { size: A4; margin: 0 } @media print { .no-print { display: none } }`}</style>
      <div className="no-print" style={{ marginBottom: 12 }}>
        <button className="btn primary" onClick={() => window.print()}>
          Print marker
        </button>{" "}
        <span style={{ color: "#555", fontSize: 13 }}>Print at 100% scale (no "fit to page").</span>
      </div>
      <h2 style={{ margin: "0 0 4mm" }}>Clearance floor marker</h2>
      <div style={{ fontSize: 12, color: "#333", marginBottom: "8mm" }}>
        Site <code>{site}</code> · AprilTag family tag36h11, id 0 · black square 160 mm
      </div>
      <img src={TAG} alt="AprilTag 36h11 id 0" style={{ width: "200mm", height: "200mm", imageRendering: "pixelated", display: "block" }} />
      <ol style={{ fontSize: 12, marginTop: "8mm", lineHeight: 1.6 }}>
        <li>Measure the black square with a ruler. If it is not 160 mm, enter the measured size on each observer phone.</li>
        <li>Tape it flat on the floor near the entrance. It becomes the origin of the twin.</li>
        <li>Keep it in place until every phone has locked its position.</li>
      </ol>
    </div>
  );
}
