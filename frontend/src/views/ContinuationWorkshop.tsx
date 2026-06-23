import { ContinuationOutlinePanel } from '../components/ContinuationOutlinePanel';
import { useProjectCtx } from '../components/Layouts';

export function ContinuationWorkshop() {
  const { project } = useProjectCtx();
  return <ContinuationOutlinePanel projectId={project.id} />;
}
