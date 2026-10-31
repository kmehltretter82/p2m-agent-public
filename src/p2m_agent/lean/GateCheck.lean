/-
Deterministic checks on a compiled candidate, run with `lean --run`.

Compares *elaborated* terms rather than source text, so a local notation, instance or
`open` in the candidate cannot quietly change what it proves. Modules are imported without
loading their extensions, so this step runs no code from the candidate.

Usage: lean --run GateCheck.lean <prove|disprove> <target> <targetModule> <solutionModule>
Prints one JSON object. The Python gate decides what the findings mean.
-/
import Lean
open Lean Meta

namespace GateCheck

/-- Remove metadata wrappers, which elaboration may add without changing the meaning. -/
partial def strip (e : Expr) : Expr :=
  e.replace fun x =>
    match x with
    | .mdata _ b => some (strip b)
    | _ => none

/-- Structural equality already ignores binder info, so compare it separately. -/
partial def sameBinderInfo : Expr → Expr → Bool
  | .forallE _ d b i, .forallE _ d' b' i' =>
    i == i' && sameBinderInfo d d' && sameBinderInfo b b'
  | .lam _ d b i, .lam _ d' b' i' =>
    i == i' && sameBinderInfo d d' && sameBinderInfo b b'
  | .app f a, .app f' a' => sameBinderInfo f f' && sameBinderInfo a a'
  | _, _ => true

def bodyOf? : ConstantInfo → Option Expr
  | .thmInfo v => some v.value
  | .defnInfo v => some v.value
  | .opaqueInfo v => some v.value
  | _ => none

def usesSorry (info : ConstantInfo) : Bool :=
  let hit (e : Expr) := (e.find? (·.isConstOf ``sorryAx)).isSome
  hit info.type || (bodyOf? info).any hit

def kindOf : ConstantInfo → String
  | .thmInfo _ => "theorem"
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "def"
  | .opaqueInfo _ => "opaque"
  | _ => "other"

def names (xs : Array Name) : Json := toJson (xs.map toString)

/-- Everything we can learn from the candidate on its own. -/
def inspectAlone (solutionModule targetModule : Name) : IO Json := do
  let env ← importModules #[{ module := solutionModule }] {} (loadExts := false)
  let some idx := env.getModuleIdx? solutionModule
    | return Json.mkObj [("error", "solution module did not load")]
  let moduleData := env.header.moduleData[idx.toNat]!
  let own := moduleData.constNames
  let ownInfos := own.filterMap env.find?
  let ownSorry := ownInfos.filter usesSorry |>.map (·.name)
  let ownAxioms := ownInfos.filter (·.isAxiom) |>.map (·.name)
  let lookalikes := own.filter fun n =>
    n != `solution && match n with
      | .str _ s => s == "solution"
      | _ => false
  let solutionFields : List (String × Json) ← match env.find? `solution with
    | none => pure [("found", toJson false)]
    | some info =>
      let declaredIn := (env.getModuleIdxFor? `solution).map fun i =>
        env.header.moduleNames[i.toNat]!
      let ctx : Core.Context := { fileName := "<gate>", fileMap := default }
      let (axioms, _) ← (collectAxioms `solution : CoreM (Array Name)).toIO ctx { env }
      pure [("found", toJson true),
            ("kind", toJson (kindOf info)),
            ("declared_in", toJson (toString declaredIn)),
            ("axioms", names axioms)]
  return Json.mkObj <| solutionFields ++ [
    ("own_sorry", names ownSorry),
    ("own_axioms", names ownAxioms),
    ("lookalikes", names lookalikes),
    ("imports_target", toJson (env.header.moduleNames.contains targetModule)),
    ("direct_imports", names (moduleData.imports.map (·.module)))]

/-- Does `solution` state exactly what the target states? -/
def compareTypes (mode : String) (target targetModule solutionModule : Name) : IO Json := do
  let env ← importModules #[{ module := targetModule }, { module := solutionModule }] {}
    (loadExts := false)
  let some targetInfo := env.find? target
    | return Json.mkObj [("error", s!"target {target} not found in {targetModule}")]
  let some solutionInfo := env.find? `solution
    | return Json.mkObj [("error", "solution not found")]
  if targetInfo.levelParams.length != solutionInfo.levelParams.length then
    return Json.mkObj [("match", "none"), ("reason", "different number of universe parameters")]
  let renamed := solutionInfo.type.instantiateLevelParams solutionInfo.levelParams
    (targetInfo.levelParams.map mkLevelParam)
  let expected := strip <| if mode == "disprove" then mkNot targetInfo.type else targetInfo.type
  let actual := strip renamed
  let binderInfoSame := sameBinderInfo expected actual
  if expected == actual then
    return Json.mkObj [("match", "structural"), ("binder_info_same", binderInfoSame)]
  match Kernel.isDefEq env {} expected actual with
  | .ok true => return Json.mkObj [("match", "defeq"), ("binder_info_same", binderInfoSame)]
  | .ok false =>
    return Json.mkObj [("match", "none"),
                       ("expected", toString expected),
                       ("actual", toString actual)]
  | .error _ => return Json.mkObj [("match", "none"), ("reason", "kernel check failed")]

def attempt (action : IO Json) : IO Json := do
  try action catch e => return Json.mkObj [("error", toString e)]

end GateCheck

def main (args : List String) : IO UInt32 := do
  let [mode, target, targetModule, solutionModule] := args
    | IO.eprintln "usage: <prove|disprove> <target> <targetModule> <solutionModule>"
      return 2
  initSearchPath (← findSysroot)
  let (t, tm, sm) := (target.toName, targetModule.toName, solutionModule.toName)
  let alone ← GateCheck.attempt (GateCheck.inspectAlone sm tm)
  let compared ← GateCheck.attempt (GateCheck.compareTypes mode t tm sm)
  IO.println (Json.mkObj [("alone", alone), ("compared", compared)]).compress
  return 0
