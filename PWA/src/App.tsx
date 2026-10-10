import Gallery from "./screens/Gallery";
import PanelScreen from "./screens/PanelScreen";
import { mockData, mockLayout, mockValues } from "./lib/mockLayout";

const isGallery = () =>
  new URLSearchParams(window.location.search).has("gallery") || window.location.pathname.endsWith("/gallery");

export default function App() {
  const gallery = isGallery();
  return (
    <>
      {gallery ? <Gallery /> : <PanelScreen layout={mockLayout} data={mockData} initialValues={mockValues} />}
      {import.meta.env.DEV && (
        <nav className="app-nav">
          <a href="/" aria-current={gallery ? undefined : "page"}>Panel</a>
          <a href="/?gallery" aria-current={gallery ? "page" : undefined}>Gallery</a>
        </nav>
      )}
    </>
  );
}
